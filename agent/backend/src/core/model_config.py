"""运行时模型配置，在线 API 与本地 Ollama 可热切换。

配置持久化到数据目录下的配置文件，保存后立即写回全局设置，
调度层下一次决策即使用新模型，无需重启服务。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.core.config import settings


class ModelConfig(BaseModel):
    """一份同时保存两种后端参数的配置，切换时不会丢失另一边的填写。"""

    provider: str = Field(default="deepseek", pattern="^(deepseek|openai|custom|ollama)$")
    # 在线 API（OpenAI 兼容，DeepSeek 为默认）
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    vision_model: str = ""
    # 本地 Ollama
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_vision_model: str = ""
    think: bool = False
    num_ctx: int = Field(default=8192, ge=1024)
    updated_at: str = ""

    @field_validator("base_url", "ollama_base_url", mode="after")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def public(self) -> dict[str, Any]:
        """给前端的视图：密钥只返回掩码。"""
        return {
            "provider": self.provider,
            "api_key": mask_key(self.api_key),
            "api_key_set": bool(self.api_key.strip()),
            "base_url": self.base_url,
            "model": self.model,
            "vision_model": self.vision_model,
            "ollama_base_url": self.ollama_base_url,
            "ollama_model": self.ollama_model,
            "ollama_vision_model": self.ollama_vision_model,
            "think": self.think,
            "num_ctx": self.num_ctx,
            "updated_at": self.updated_at,
        }


def mask_key(key: str) -> str:
    raw = (key or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "****"
    return f"{raw[:4]}****{raw[-4:]}"


def _config_path() -> Path:
    return settings.paths.data_dir / "model_config.json"


def _defaults() -> ModelConfig:
    llm = settings.llm
    return ModelConfig(
        provider=llm.provider,  # type: ignore[arg-type]
        api_key=llm.api_key or "",
        base_url=llm.base_url,
        model=llm.model,
        vision_model=llm.vision_model,
        ollama_model=settings.llm.model if llm.provider == "ollama" else "qwen3:8b",
        think=llm.think,
        num_ctx=llm.num_ctx,
    )


_CONFIG: ModelConfig | None = None


def get_config() -> ModelConfig:
    global _CONFIG
    if _CONFIG is None:
        path = _config_path()
        if path.exists():
            try:
                _CONFIG = ModelConfig(**json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 - 配置损坏时退回 .env 默认值
                _CONFIG = _defaults()
        else:
            _CONFIG = _defaults()
    _apply(_CONFIG)
    return _CONFIG


def _apply(cfg: ModelConfig) -> None:
    """把当前配置写回全局 settings，供调度层与视觉模型选择复用。"""
    llm = settings.llm
    llm.provider = cfg.provider  # type: ignore[assignment]
    llm.think = cfg.think
    llm.num_ctx = cfg.num_ctx
    if cfg.provider == "ollama":
        llm.base_url = cfg.ollama_base_url
        llm.model = cfg.ollama_model
        llm.vision_model = cfg.ollama_vision_model or cfg.ollama_model
    else:
        llm.api_key = cfg.api_key or None
        llm.base_url = cfg.base_url
        llm.model = cfg.model
        llm.vision_model = cfg.vision_model or cfg.model


def active_model(image: bool = False) -> str:
    """当前生效的模型名；请求多模态模型时返回视觉模型，未单独配置则回退主模型。"""
    cfg = get_config()
    if cfg.provider == "ollama":
        return (cfg.ollama_vision_model or cfg.ollama_model) if image else cfg.ollama_model
    return (cfg.vision_model or cfg.model) if image else cfg.model


def build_config(payload: dict[str, Any]) -> ModelConfig:
    """按前端提交的值构造配置；密钥为空或等于掩码时保持原值。"""
    old = get_config()
    key = str(payload.get("api_key") or "").strip()
    if not key or key == mask_key(old.api_key):
        key = old.api_key

    return ModelConfig(
        provider=str(payload.get("provider") or old.provider),
        api_key=key,
        base_url=str(payload.get("base_url") or old.base_url),
        model=str(payload.get("model") or old.model),
        vision_model=str(payload["vision_model"]) if "vision_model" in payload else old.vision_model,
        ollama_base_url=str(payload.get("ollama_base_url") or old.ollama_base_url),
        ollama_model=str(payload.get("ollama_model") or old.ollama_model),
        ollama_vision_model=(
            str(payload["ollama_vision_model"])
            if "ollama_vision_model" in payload
            else old.ollama_vision_model
        ),
        think=bool(payload.get("think", old.think)),
        num_ctx=int(payload.get("num_ctx") or old.num_ctx),
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def save_config(payload: dict[str, Any]) -> ModelConfig:
    """校验并持久化配置，随后立即应用（下一次决策生效）。"""
    global _CONFIG
    cfg = build_config(payload)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
    _CONFIG = cfg
    _apply(cfg)
    return cfg


def reload_config() -> ModelConfig:
    global _CONFIG
    _CONFIG = None
    return get_config()
