"""统一模型路由（agent_llm.py）：按运行时配置切换在线 API / 本地 Ollama。"""

from __future__ import annotations

from typing import Any, Iterator

from src.core.model_config import get_config
from src.generation.llm import ChatMessage, LLMClient as ApiLLMClient
from src.generation.ollama_llm import OllamaLLM, list_local_models

_AGENT_LLM: "AgentLLM | None" = None


class AgentLLM:
    """对调度层暴露统一的 chat / chat_stream，内部按 provider 分发。"""

    def __init__(self) -> None:
        self._api: ApiLLMClient | None = None
        self._api_sig: tuple[str, str, str] | None = None
        self._local: OllamaLLM | None = None
        self._local_sig: tuple[str, str] | None = None

    def _api_client(self) -> ApiLLMClient:
        cfg = get_config()
        sig = (cfg.api_key, cfg.base_url, cfg.model)
        if self._api is None or self._api_sig != sig:
            self._api = ApiLLMClient(
                api_key=cfg.api_key or None,
                base_url=cfg.base_url,
                model=cfg.model,
            )
            self._api_sig = sig
        return self._api

    def _local_client(self) -> OllamaLLM:
        cfg = get_config()
        sig = (cfg.ollama_base_url, cfg.ollama_model)
        if self._local is None or self._local_sig != sig:
            self._local = OllamaLLM(
                base_url=cfg.ollama_base_url,
                model=cfg.ollama_model,
            )
            self._local_sig = sig
        return self._local

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        cfg = get_config()
        if cfg.provider == "ollama":
            return self._local_client().chat(
                messages, tools=tools, model=model,
                temperature=temperature, max_tokens=max_tokens,
            )
        return self._api_client().chat(
            messages, tools=tools, model=model,
            temperature=temperature, max_tokens=max_tokens,
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
        cfg = get_config()
        if cfg.provider == "ollama":
            yield from self._local_client().chat_stream(
                messages, tools=tools, model=model,
                temperature=temperature, max_tokens=max_tokens,
            )
            return
        yield from self._api_client().chat_stream(
            messages, tools=tools, model=model,
            temperature=temperature, max_tokens=max_tokens,
        )

    def is_ready(self) -> tuple[bool, str]:
        cfg = get_config()
        if cfg.provider == "ollama":
            return self._local_client().is_ready()
        if not cfg.api_key.strip():
            return False, "未配置在线 API Key，请在「模型配置」中填写"
        return True, f"已配置在线 API（{cfg.base_url}，模型 {cfg.model}）"

    def info(self) -> dict[str, Any]:
        cfg = get_config()
        ready, why = self.is_ready()
        return {
            "name": "ollama" if cfg.provider == "ollama" else "api",
            "provider": cfg.provider,
            "model": cfg.ollama_model if cfg.provider == "ollama" else cfg.model,
            "base_url": cfg.ollama_base_url if cfg.provider == "ollama" else cfg.base_url,
            "ready": ready,
            "reason": why,
        }

    def local_models(self) -> list[str]:
        return list_local_models(get_config().ollama_base_url)


def get_agent_llm() -> AgentLLM:
    global _AGENT_LLM
    if _AGENT_LLM is None:
        _AGENT_LLM = AgentLLM()
    return _AGENT_LLM


def reset_agent_llm() -> None:
    """配置保存后丢弃缓存的客户端，下一次调用按新配置重建。"""
    global _AGENT_LLM
    _AGENT_LLM = None
