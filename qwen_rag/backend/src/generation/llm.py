"""LLM 调用（llm.py）：本地 Ollama 原生 ``/api/chat``。

与 agent 版的差异：不再走 DeepSeek / OpenAI 兼容接口，而是调用本机 Ollama 中的
Qwen3（``qwen3:8b``）。Ollama 原生接口才有 ``think`` 开关——关闭 thinking 后单轮
决策从约 10s 降到约 1s；同时原生接口的 tool_calls ``arguments`` 直接是对象，流式
按 NDJSON 逐行下发，无需 ``stream_options``。

对外只暴露 Agent 调度层需要的两个方法：``chat``（非流式）与 ``chat_stream``（流式，
先 yield 正文增量，最后 yield 聚合后的 :class:`ChatMessage`）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from src.core.config import settings


@dataclass
class ChatMessage:
    """一次决策调用的结果：``tool_calls`` 为空时 ``content`` 即最终答案。"""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    model: str = ""


class LLMClient:
    """Ollama 客户端封装。用法：``LLMClient().chat_stream(messages, tools=...)``。"""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        client: Any = None,
    ) -> None:
        cfg = settings.llm
        self.base_url = (base_url or cfg.base_url).rstrip("/")
        self.model = model or cfg.model
        self.timeout = timeout or cfg.timeout
        self.max_retries = cfg.max_retries if max_retries is None else max_retries
        self._client = client  # 允许注入（测试用 mock）
        self.last_seconds: float = 0.0

    @property
    def client(self) -> httpx.Client:
        """惰性创建 HTTP 客户端；连接超时单独收紧，避免 Ollama 未启动时久等。"""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            )
        return self._client

    def is_ready(self) -> tuple[bool, str]:
        """检查本地 Ollama 是否可用（供 /health 使用）。"""
        try:
            resp = self.client.get("/api/tags", timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            return False, f"无法连接本地 Ollama（{self.base_url}）：{type(exc).__name__}"
        if resp.status_code != 200:
            return False, f"Ollama 返回 {resp.status_code}"
        return True, f"已连接本地 Ollama（{self.base_url}，模型 {self.model}）"

    # ---- 请求组装 ----

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: bool,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        cfg = settings.llm
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": self._to_ollama_messages(messages),
            "stream": stream,
            "think": cfg.think,
            "options": {
                "temperature": cfg.temperature if temperature is None else temperature,
                "top_p": cfg.top_p,
                "num_predict": cfg.max_tokens if max_tokens is None else max_tokens,
                "num_ctx": cfg.num_ctx,
            },
        }
        if tools:
            payload["tools"] = tools
        return payload

    @staticmethod
    def _to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """OpenAI 风格消息 → Ollama 原生消息。

        agent 调度层追加的是 ``assistant(tool_calls)`` + ``tool(tool_call_id)``：
        Ollama 要求 ``arguments`` 是对象而非 JSON 字符串，tool 消息则可带 ``tool_name``。
        """
        out: list[dict[str, Any]] = []
        name_by_id: dict[str, str] = {}

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")
            if isinstance(content, list):  # 兼容内容块数组（图片已下线，只拼文本）
                content = "".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )

            if role == "assistant":
                calls: list[dict[str, Any]] = []
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    raw = fn.get("arguments")
                    if isinstance(raw, str):
                        try:
                            args = json.loads(raw) if raw.strip() else {}
                        except json.JSONDecodeError:
                            args = {}
                    else:
                        args = raw or {}
                    if tc.get("id"):
                        name_by_id[str(tc["id"])] = str(fn.get("name", ""))
                    calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
                item: dict[str, Any] = {"role": "assistant", "content": content or ""}
                if calls:
                    item["tool_calls"] = calls
                out.append(item)
            elif role == "tool":
                item = {"role": "tool", "content": content or ""}
                tool_name = name_by_id.get(str(msg.get("tool_call_id", "")))
                if tool_name:
                    item["tool_name"] = tool_name
                out.append(item)
            else:
                out.append({"role": role, "content": content or ""})
        return out

    @staticmethod
    def _normalize_calls(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Ollama 的 tool_calls → 调度层约定格式（arguments 统一成 JSON 字符串）。"""
        calls: list[dict[str, Any]] = []
        for i, tc in enumerate(raw or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args or {}, ensure_ascii=False)
            calls.append(
                {
                    "id": tc.get("id") or f"call_{i}",
                    "name": fn.get("name", ""),
                    "arguments": args,
                }
            )
        return calls

    @staticmethod
    def _usage(data: dict[str, Any]) -> dict[str, Any]:
        prompt = data.get("prompt_eval_count")
        completion = data.get("eval_count")
        usage: dict[str, Any] = {}
        if prompt is not None:
            usage["prompt_tokens"] = prompt
        if completion is not None:
            usage["completion_tokens"] = completion
        if prompt is not None and completion is not None:
            usage["total_tokens"] = prompt + completion
        return usage

    @staticmethod
    def _http_error(resp: httpx.Response) -> RuntimeError:
        detail = resp.read().decode("utf-8", "replace")[:300]
        return RuntimeError(f"Ollama 返回 {resp.status_code}：{detail}")

    # ---- 调用入口 ----

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        """单次决策调用（非流式）。"""
        payload = self._payload(
            messages, stream=False, model=model, temperature=temperature,
            max_tokens=max_tokens, tools=tools,
        )
        t0 = time.perf_counter()
        last_exc: Exception | None = None
        data: dict[str, Any] | None = None

        for attempt in range(1, self.max_retries + 2):
            try:
                resp = self.client.post("/api/chat", json=payload)
                if resp.status_code >= 400:
                    raise self._http_error(resp)
                data = resp.json()
                break
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt > self.max_retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))

        if data is None:
            raise RuntimeError(
                f"无法连接本地 Ollama（{self.base_url}）：请先启动 ollama serve"
            ) from last_exc

        msg = data.get("message") or {}
        self.last_seconds = time.perf_counter() - t0
        return ChatMessage(
            content=(msg.get("content") or "").strip(),
            tool_calls=self._normalize_calls(msg.get("tool_calls")),
            usage=self._usage(data),
            finish_reason=data.get("done_reason") or "",
            model=data.get("model") or (model or self.model),
        )

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[tuple[str, Any]]:
        """流式决策调用。

        yield ``("delta", 文本增量)``；结束 yield ``("done", ChatMessage)``，
        其中已聚合正文与 tool_calls。Ollama 的思维链在 ``thinking`` 字段，直接丢弃。
        """
        payload = self._payload(
            messages, stream=True, model=model, temperature=temperature,
            max_tokens=max_tokens, tools=tools,
        )
        t0 = time.perf_counter()
        pieces: list[str] = []
        calls: list[dict[str, Any]] = []
        done: dict[str, Any] = {}
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 2):
            emitted = False
            try:
                with self.client.stream("POST", "/api/chat", json=payload) as resp:
                    if resp.status_code >= 400:
                        raise self._http_error(resp)
                    for line in resp.iter_lines():
                        if not line.strip():
                            continue
                        obj = json.loads(line)
                        msg = obj.get("message") or {}
                        text = msg.get("content")
                        if text:
                            pieces.append(text)
                            emitted = True
                            yield ("delta", text)
                        if msg.get("tool_calls"):
                            calls = msg["tool_calls"]
                            emitted = True
                        if obj.get("done"):
                            done = obj
                break
            except httpx.TransportError as exc:
                last_exc = exc
                # 已经吐过字再重试会造成正文重复，直接上抛
                if emitted or attempt > self.max_retries:
                    raise RuntimeError(f"本地模型流式输出中断：{exc}") from exc
                time.sleep(min(2 ** (attempt - 1), 4))
        else:
            raise RuntimeError(
                f"无法连接本地 Ollama（{self.base_url}）：请先启动 ollama serve"
            ) from last_exc

        latest = done or {}
        self.last_seconds = time.perf_counter() - t0
        yield (
            "done",
            ChatMessage(
                content="".join(pieces).strip(),
                tool_calls=self._normalize_calls(calls),
                usage=self._usage(latest),
                finish_reason=latest.get("done_reason") or "",
                model=latest.get("model") or (model or self.model),
            ),
        )

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
            "think": settings.llm.think,
            "num_ctx": settings.llm.num_ctx,
        }


_CLIENT: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LLMClient()
    return _CLIENT
