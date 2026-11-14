import time

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import SystemSetting

MODEL_CONFIG_KEY = "model_config"
PROVIDERS = ("api", "ollama")


def default_model_config() -> dict:
    """来自环境变量的默认配置，数据库保存后以数据库为准。"""
    provider = settings.llm_provider if settings.llm_provider in PROVIDERS else "api"
    return {
        "provider": provider,
        "api_base_url": settings.llm_base_url.rstrip("/"),
        "api_model": settings.llm_model,
        "ollama_base_url": settings.ollama_base_url.rstrip("/"),
        "ollama_model": settings.ollama_model,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "updated_at": "",
    }


async def get_model_config(db: AsyncSession) -> dict:
    row = await db.get(SystemSetting, MODEL_CONFIG_KEY)
    config = default_model_config()
    if row and row.value:
        config.update({key: value for key, value in row.value.items() if value is not None})
    if config["provider"] not in PROVIDERS:
        config["provider"] = "api"
    config["api_base_url"] = str(config["api_base_url"]).rstrip("/")
    config["ollama_base_url"] = str(config["ollama_base_url"]).rstrip("/")
    return config


def active_endpoint(config: dict) -> tuple[str, str]:
    """当前生效的接口地址与模型名。"""
    if config.get("provider") == "ollama":
        return config["ollama_base_url"].rstrip("/"), config["ollama_model"]
    return config["api_base_url"].rstrip("/"), config["api_model"]


async def update_model_config(db: AsyncSession, values: dict) -> dict:
    row = await db.get(SystemSetting, MODEL_CONFIG_KEY)
    merged = default_model_config()
    if row and row.value:
        merged.update(row.value)
    merged.update({key: value for key, value in values.items() if value is not None})
    merged["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if row is None:
        db.add(SystemSetting(key=MODEL_CONFIG_KEY, value=merged))
    else:
        row.value = merged
    await db.commit()
    return merged
