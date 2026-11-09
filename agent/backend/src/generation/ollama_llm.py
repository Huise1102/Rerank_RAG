"""本地 Ollama 客户端，与在线 API 客户端同构，提供同步与流式对话。

Ollama 原生接口支持思考开关与逐行流式输出，
工具调用参数直接是对象，这里统一转成 JSON 字符串。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from src.core.config import settings
from src.generation.llm import ChatMessage

_IMAGE_PREFIX = "data:image/"


class OllamaLLM:
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
        self._client = client
        self.last_seconds: float = 0.0

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            )
        return self._client

    def is_ready(self) -> tuple[bool, str]:
        try:
            resp = self.client.get("/api/tags", timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            return False, f"无法连接本地 Ollama（{self.base_url}）：{type(exc).__name__}"
        if resp.status_code != 200:
            return False, f"Ollama 返回 {resp.status_code}"
        names = [m.get("name", "") for m in resp.json().get("models", [])]
        if self.model and self.model not in names:
            return False, f"本地未找到模型 {self.model}（已安装：{', '.join(names) or '无'}）"
        return True, f"已连接本地 Ollama（{self.base_url}，模型 {self.model}）"

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
        """把 OpenAI 风格消息转成 Ollama 原生消息，并保留图片数据链接。"""
        out: list[dict[str, Any]] = []
        name_by_id: dict[str, str] = {}

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")
            images: list[str] = []
            if isinstance(content, list):
                texts: list[str] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        texts.append(str(part.get("text", "")))
                    elif part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        if isinstance(url, str) and url.startswith(_IMAGE_PREFIX):
                            images.append(url.split(",", 1)[-1])
                content = "".join(texts)

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
                item = {"role": role, "content": content or ""}
                if images:
                    item["images"] = images
                out.append(item)
        return out

    @staticmethod
    def _normalize_calls(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
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

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatMessage:
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
            "ready": ready,
            "reason": why,
            "think": settings.llm.think,
            "num_ctx": settings.llm.num_ctx,
        }


def list_local_models(base_url: str, timeout: float = 8.0) -> list[str]:
    resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
    resp.raise_for_status()
    return [m.get("name", "") for m in resp.json().get("models", []) if m.get("name")]
