"""LLM 调用（llm.py）：用 OpenAI SDK 调 DeepSeek 兼容接口。

``base_url`` 存根地址（``https://api.deepseek.com``）；带 ``/chat/completions`` 的完整
路径会被 SDK 再拼一次而 404。重试分两层：SDK 管连接错误/429/5xx 且不重试超时，本模块
只重试超时并在重试前检查剩余总预算。显式指定 model 时不降级；API Key 只读环境变量/.env。

（从 demo/src/generation/llm.py 逐字复制，仅把 config 导入改为 src.core.config。）
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

from src.core.config import settings
from src.generation.citation import CitationReport, extract_citations
from src.generation.prompt import (
    BuiltPrompt,
    PromptBuilder,
    PromptContext,
    build_messages,
    contexts_from_results,
)

#: 可重试异常的类型名（按名字匹配，避免硬依赖 openai 异常类导致导入失败）
_RETRYABLE = {
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "RateLimitError",
    "APIStatusError",
}


@dataclass
class LLMResponse:
    """一次生成的完整结果。"""

    answer: str
    contexts: list[PromptContext] = field(default_factory=list)
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0
    first_token_seconds: float | None = None
    attempts: int = 1
    finish_reason: str = ""
    built_prompt: BuiltPrompt | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def citations(self) -> CitationReport:
        """按需解析引用（惰性，避免每次生成都解析）。"""
        cached = getattr(self, "_citations", None)
        if cached is None:
            cached = extract_citations(self.answer, self.contexts)
            object.__setattr__(self, "_citations", cached)
        return cached

    def to_dict(self, include_contexts: bool = True, include_citations: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "answer": self.answer,
            "model": self.model,
            "usage": self.usage,
            "elapsed": round(self.elapsed, 3),
            "first_token_seconds": (
                round(self.first_token_seconds, 3) if self.first_token_seconds is not None else None
            ),
            "attempts": self.attempts,
            "finish_reason": self.finish_reason,
            "warnings": self.warnings,
        }
        if include_citations:
            data["citation_report"] = self.citations.to_dict()
        if include_contexts:
            data["contexts"] = [
                {
                    "index": c.index,
                    "chunk_id": c.chunk_id,
                    "source": c.source,
                    "page": c.page,
                    "score": c.score,
                }
                for c in self.contexts
            ]
        return data

    def summary(self) -> str:
        tok = self.usage.get("total_tokens")
        first = f" | 首字 {self.first_token_seconds:.2f}s" if self.first_token_seconds else ""
        return (
            f"{len(self.answer)} 字 | {self.model} | {self.elapsed:.2f}s{first}"
            + (f" | {tok} tokens" if tok else "")
            + f" | 尝试 {self.attempts} 次 | {self.citations.summary()}"
        )


class LLMClient:
    """DeepSeek 客户端封装。用法：``LLMClient().generate(query, contexts)``。"""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        prompt_builder: PromptBuilder | None = None,
        client: Any = None,
    ) -> None:
        cfg = settings.llm
        self.api_key = api_key
        self.base_url = (base_url or cfg.base_url).rstrip("/")
        self.model = model or cfg.model
        self.timeout = timeout or cfg.timeout
        self.max_retries = cfg.max_retries if max_retries is None else max_retries
        self.builder = prompt_builder or PromptBuilder()
        self._client = client  # 允许注入（测试用 mock，无需真实 Key）
        self.last_seconds: float = 0.0
    @property
    def client(self) -> Any:
        """惰性创建 OpenAI 客户端。"""
        if self._client is None:
            from openai import OpenAI

            # require_api_key 未配置时抛带操作指引的异常，而不是等到 401
            key = self.api_key or settings.llm.require_api_key()
            self._client = OpenAI(
                api_key=key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._client

    def is_ready(self) -> tuple[bool, str]:
        """检查是否具备调用条件（供 API 的 /health 使用）。"""
        if self._client is not None:
            return True, "已注入客户端"
        if self.api_key or settings.llm.is_configured:
            return True, f"已配置 Key（{self.base_url}）"
        return False, "未配置 DeepSeek API Key（请在 .env 设置 RAG_LLM__API_KEY）"
    def _params(self, messages: list[dict[str, str]], *, stream: bool, model: str | None = None,
                temperature: float | None = None, max_tokens: int | None = None) -> dict[str, Any]:
        cfg = settings.llm
        return {
            "model": model or self.model,
            "messages": messages,
            "temperature": cfg.temperature if temperature is None else temperature,
            "top_p": cfg.top_p,
            "max_tokens": cfg.max_tokens if max_tokens is None else max_tokens,
            "stream": stream,
        }

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        return type(exc).__name__ in _RETRYABLE

    def _call_with_retry(self, params: dict[str, Any], *, deadline: float) -> Any:
        """带总预算约束的调用：SDK 已重试连接错误/429/5xx，这里只额外重试超时，
        每次重试前检查剩余时间，不够就放弃。
        """
        cfg = settings.llm
        last_exc: Exception | None = None

        for attempt in range(1, cfg.max_retries + 2):
            remaining = deadline - time.perf_counter()
            if remaining <= 1.0:
                raise TimeoutError(
                    f"LLM 调用超出总时间预算（{self.timeout}s）。最后一次错误：{last_exc}"
                ) from last_exc
            try:
                # 动态收紧单次超时，避免单次请求吃掉全部预算
                self.client.timeout = max(5.0, min(float(self.timeout), remaining))
                return self.client.chat.completions.create(**params)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                name = type(exc).__name__
                if not self._is_retryable(exc):
                    raise
                if attempt > cfg.max_retries:
                    break
                # 指数退避 + 抖动，避免多个请求同时重试造成尖峰
                backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
                if time.perf_counter() + backoff >= deadline:
                    break
                time.sleep(backoff)
        raise last_exc if last_exc else RuntimeError("LLM 调用失败")
    def generate(
        self,
        query: str,
        contexts: Sequence[PromptContext] | Sequence[Any] | None = None,
        *,
        history: Sequence[tuple[str, str]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_fallback_models: bool = True,
    ) -> LLMResponse:
        """生成答案。

        :param contexts: 上下文（PromptContext、RetrievalResult 或字典均可）
        :param model: 显式指定模型；指定后不再降级（``use_fallback_models`` 随之失效）
        :returns: LLMResponse（``answer`` + ``contexts`` + 用量 + 引用报告）
        """
        items = self._normalize_contexts(contexts)
        built = self.builder.build(query, items, history=history)
        warnings: list[str] = []

        # 无上下文不烧 token：没有资料时模型的回答就是幻觉的温床，故直接返回配置话术
        if not built.contexts:
            warnings.append("无检索上下文，返回兜底话术（未调用 LLM）")
            resp = LLMResponse(
                answer=self.builder.no_context_reply, contexts=[], model=model or self.model,
                usage={}, elapsed=0.0, attempts=0, built_prompt=built, warnings=warnings,
            )
            # 兜底话术本身也是拒答，让引用解析能看到这一点
            object.__setattr__(resp, "_citations", extract_citations(resp.answer, []))
            return resp

        models = [model or self.model]
        if use_fallback_models and not model:
            models += [m for m in settings.llm.fallback_models if m not in models]

        t_start = time.perf_counter()
        deadline = t_start + self.timeout
        attempts = 0
        last_exc: Exception | None = None

        for candidate in models:
            params = self._params(built.messages, stream=False, model=candidate,
                                  temperature=temperature, max_tokens=max_tokens)
            t0 = time.perf_counter()
            try:
                completion = self._call_with_retry(params, deadline=deadline)
                attempts += 1
                elapsed = time.perf_counter() - t0
                self.last_seconds = elapsed

                answer = (completion.choices[0].message.content or "").strip()
                usage = {}
                if getattr(completion, "usage", None) is not None:
                    usage = {
                        "prompt_tokens": getattr(completion.usage, "prompt_tokens", None),
                        "completion_tokens": getattr(completion.usage, "completion_tokens", None),
                        "total_tokens": getattr(completion.usage, "total_tokens", None),
                    }
                finish = getattr(completion.choices[0], "finish_reason", "") or ""
                if finish == "length":
                    warnings.append("回答因达到 max_tokens 被截断，可调大 llm.max_tokens")

                # 推理模型的思维链放在 reasoning_content，只记录长度便于观察
                reasoning = getattr(completion.choices[0].message, "reasoning_content", None)
                if reasoning:
                    warnings.append(f"模型返回了思维链（{len(reasoning)} 字），已忽略不计入答案")

                return LLMResponse(
                    answer=answer,
                    contexts=built.contexts,
                    model=candidate,
                    usage=usage,
                    elapsed=elapsed,
                    attempts=attempts,
                    finish_reason=finish,
                    built_prompt=built,
                    warnings=warnings,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                attempts += 1
                warnings.append(f"模型 {candidate} 调用失败: {type(exc).__name__}: {exc}")
                # 指定模型时不换模型；不可重试异常（参数错误/鉴权失败）换模型也无用，直接抛
                if model or not self._is_retryable(exc):
                    raise
                if time.perf_counter() >= deadline:
                    break
                continue

        raise RuntimeError(
            "所有模型均调用失败：\n" + "\n".join(f"  - {w}" for w in warnings)
        ) from last_exc
    def stream(
        self,
        query: str,
        contexts: Sequence[PromptContext] | Sequence[Any] | None = None,
        *,
        history: Sequence[tuple[str, str]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        """流式生成，逐段 yield 文本增量。需要完整结果与引用时用 :meth:`stream_response`。"""
        items = self._normalize_contexts(contexts)
        built = self.builder.build(query, items, history=history)

        if not built.contexts:
            yield self.builder.no_context_reply
            return

        params = self._params(built.messages, stream=True, model=model,
                             temperature=temperature, max_tokens=max_tokens)
        stream = self._call_with_retry(params, deadline=time.perf_counter() + self.timeout)
        for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0].delta, "content", None)
            if delta:
                if on_delta:
                    on_delta(delta)
                yield delta

    def stream_response(
        self,
        query: str,
        contexts: Sequence[PromptContext] | Sequence[Any] | None = None,
        **kwargs: Any,
    ) -> tuple[Iterator[str], dict[str, Any]]:
        """返回 (增量迭代器, 结果容器)；迭代结束后 ``box["response"]`` 是完整 LLMResponse。"""
        items = self._normalize_contexts(contexts)
        history = kwargs.pop("history", None)
        built = self.builder.build(query, items, history=history)
        box: dict[str, Any] = {"response": None}

        def _iter() -> Iterator[str]:
            t0 = time.perf_counter()
            first: float | None = None
            pieces: list[str] = []
            warnings: list[str] = []

            if not built.contexts:
                text = self.builder.no_context_reply
                yield text
                resp = LLMResponse(answer=text, contexts=[], model=kwargs.get("model") or self.model,
                                   elapsed=0.0, attempts=0, built_prompt=built,
                                   warnings=["无检索上下文，返回兜底话术"])
                object.__setattr__(resp, "_citations", extract_citations(text, []))
                box["response"] = resp
                return

            try:
                for delta in self.stream(query, built.contexts, history=history, **kwargs):
                    if first is None:
                        first = time.perf_counter() - t0
                    pieces.append(delta)
                    yield delta
            except Exception as exc:  # noqa: BLE001 - 流式中途失败也要给出可用的收尾
                warnings.append(f"流式中断: {type(exc).__name__}: {exc}")
            finally:
                answer = "".join(pieces).strip()
                resp = LLMResponse(
                    answer=answer or self.builder.no_context_reply,
                    contexts=built.contexts,
                    model=kwargs.get("model") or self.model,
                    elapsed=time.perf_counter() - t0,
                    first_token_seconds=first,
                    attempts=1,
                    built_prompt=built,
                    warnings=warnings,
                )
                box["response"] = resp

        return _iter(), box
    @staticmethod
    def _normalize_contexts(contexts: Sequence[Any] | None) -> list[PromptContext]:
        if not contexts:
            return []
        items = list(contexts)
        if items and not isinstance(items[0], PromptContext):
            return contexts_from_results(items)
        return items

    def info(self) -> dict[str, Any]:
        ready, why = self.is_ready()
        return {
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "ready": ready,
            "reason": why,
            "temperature": settings.llm.temperature,
            "max_tokens": settings.llm.max_tokens,
            "fallback_models": list(settings.llm.fallback_models),
        }


_CLIENT: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端单例。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LLMClient()
    return _CLIENT


def generate(
    query: str,
    contexts: Sequence[PromptContext] | Sequence[Any] | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """生成模块的统一入口：检索结果 -> 答案。

    :param contexts: 可直接传检索的 ``resp.results``
    :returns: LLMResponse，``.answer`` 为答案文本，``.contexts`` 为实际使用的上下文
    """
    return get_llm_client().generate(query, contexts, **kwargs)


def generate_with_retrieval(
    query: str,
    *,
    retrieval_strategy: str = "hybrid_rerank",
    chunk_strategy: str | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """端到端：检索 + 生成。单一入口，避免 API 层与评估层各写一遍而产生行为差异。"""
    from src.retrieval.pipeline import retrieve

    retrieval = retrieve(query, strategy=retrieval_strategy, chunk_strategy=chunk_strategy)
    response = generate(query, retrieval.results, **kwargs)
    # 检索耗时并入总耗时，评估延迟时才是真实的端到端数字
    response.usage.setdefault("retrieval_timing", retrieval.timing)
    response.warnings.extend(retrieval.warnings)
    return response
