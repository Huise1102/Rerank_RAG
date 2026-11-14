from fastapi import HTTPException, status
from langchain_openai import ChatOpenAI

from src.core.config import settings


def llm_available(config: dict | None = None) -> bool:
    provider = (config or {}).get("provider", settings.llm_provider)
    if provider == "ollama":
        return True
    return bool(settings.llm_api_key)


def build_chat_model(config: dict, streaming: bool = True) -> ChatOpenAI:
    """按配置构建对话模型，本地 Ollama 走其 OpenAI 兼容接口。"""
    provider = config.get("provider", settings.llm_provider)
    if provider == "ollama":
        base_url = str(config.get("ollama_base_url", settings.ollama_base_url)).rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        api_key = settings.llm_api_key or "ollama"
        model = config.get("ollama_model", settings.ollama_model)
    else:
        if not settings.llm_api_key:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="尚未配置大模型密钥，请在项目根 .env 中设置 LLM_API_KEY",
            )
        base_url = str(config.get("api_base_url", settings.llm_base_url)).rstrip("/")
        api_key = settings.llm_api_key
        model = config.get("api_model", settings.llm_model)

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=config.get("temperature", settings.llm_temperature),
        max_tokens=config.get("max_tokens", settings.llm_max_tokens),
        timeout=settings.llm_timeout_seconds,
        streaming=streaming,
    )
