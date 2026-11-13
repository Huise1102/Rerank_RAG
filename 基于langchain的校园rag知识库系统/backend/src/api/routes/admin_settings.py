import asyncio

import httpx
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import require_admin
from src.core.config import settings
from src.db.models import AuditLog, User
from src.db.session import get_db
from src.generation.llm import build_chat_model
from src.schemas.knowledge import ModelConfigOut, ModelConfigRequest
from src.services.setting_service import (
    active_endpoint,
    get_model_config,
    update_model_config,
)

router = APIRouter(prefix="/admin/model-config", tags=["模型配置"])


def _to_out(config: dict) -> ModelConfigOut:
    base_url, model = active_endpoint(config)
    return ModelConfigOut(
        provider=config["provider"],
        api_base_url=config["api_base_url"],
        api_model=config["api_model"],
        ollama_base_url=config["ollama_base_url"],
        ollama_model=config["ollama_model"],
        temperature=float(config["temperature"]),
        max_tokens=int(config["max_tokens"]),
        api_key_configured=bool(settings.llm_api_key),
        active_model=model,
        active_base_url=base_url,
        updated_at=config.get("updated_at", ""),
    )


def _fetch_ollama_models(base_url: str, timeout: float = 8.0) -> list[str]:
    resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
    resp.raise_for_status()
    return [item.get("name", "") for item in resp.json().get("models", []) if item.get("name")]


@router.get("", response_model=ModelConfigOut)
async def read_model_config(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return _to_out(await get_model_config(db))


@router.put("", response_model=ModelConfigOut)
async def write_model_config(
    payload: ModelConfigRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    config = await update_model_config(db, payload.model_dump())
    base_url, model = active_endpoint(config)
    source = "本地模型" if config["provider"] == "ollama" else "在线 API"
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="update_model_config",
            target_type="system_setting",
            target_id="model_config",
            detail=f"模型来源改为{source}，接口 {base_url}，模型 {model}",
        )
    )
    await db.commit()
    return _to_out(config)


@router.get("/ollama-models")
async def list_ollama_models(
    base_url: str | None = Query(default=None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    config = await get_model_config(db)
    target = (base_url or config["ollama_base_url"]).rstrip("/")
    try:
        models = await asyncio.to_thread(_fetch_ollama_models, target)
    except Exception as exc:
        return {
            "ok": False,
            "models": [],
            "base_url": target,
            "message": f"无法连接本地 Ollama（{target}）：{exc}",
        }
    return {"ok": True, "models": models, "base_url": target}


@router.post("/test")
async def test_model_connection(
    payload: ModelConfigRequest | None = Body(default=None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    config = payload.model_dump() if payload is not None else await get_model_config(db)
    base_url, model = active_endpoint(config)

    if config["provider"] == "ollama":
        try:
            installed = await asyncio.to_thread(_fetch_ollama_models, base_url)
        except Exception as exc:
            return {"ok": False, "message": f"无法连接本地 Ollama（{base_url}）：{exc}"}
        if model not in installed:
            return {
                "ok": False,
                "message": f"本地未安装模型 {model}（已安装：{', '.join(installed) or '无'}）",
            }
    elif not settings.llm_api_key:
        return {"ok": False, "message": "尚未配置 LLM_API_KEY，无法测试在线 API"}

    try:
        chat = build_chat_model(config, streaming=False)
        reply = await chat.ainvoke("请只回复：连接正常")
        source = "本地模型" if config["provider"] == "ollama" else "在线 API"
        return {
            "ok": True,
            "message": f"{source}可用：{model}",
            "reply": str(reply.content)[:100],
        }
    except Exception as exc:
        return {"ok": False, "message": f"连接失败：{exc}"}
