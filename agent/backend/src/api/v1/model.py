"""模型配置 API：读取 / 保存 / 连通性测试 / 本地模型列表。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.concurrency import run_in_threadpool

from src.core.model_config import build_config, get_config, save_config
from src.generation.agent_llm import reset_agent_llm
from src.generation.llm import LLMClient as ApiLLMClient
from src.generation.ollama_llm import OllamaLLM, list_local_models

router = APIRouter(prefix="/model-config", tags=["模型配置"])


@router.get("", summary="读取当前模型配置")
async def read_model_config() -> dict[str, Any]:
    return get_config().public()


@router.put("", summary="保存模型配置（立即生效）")
async def update_model_config(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg = save_config(payload)
    reset_agent_llm()
    return cfg.public()


@router.get("/ollama-models", summary="列出本地 Ollama 已安装模型")
async def ollama_models(base_url: str | None = Query(default=None)) -> dict[str, Any]:
    cfg = get_config()
    url = (base_url or cfg.ollama_base_url).rstrip("/")
    try:
        models = await run_in_threadpool(list_local_models, url)
        return {"ok": True, "models": models, "base_url": url}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "models": [], "base_url": url, "message": _error_text(exc)}


@router.post("/test", summary="测试当前填写的模型是否可用")
async def test_model_config(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg = build_config(payload)
    try:
        if cfg.provider == "ollama":
            models = await run_in_threadpool(list_local_models, cfg.ollama_base_url)
            if cfg.ollama_model not in models:
                return {
                    "ok": False,
                    "message": f"本地未找到模型 {cfg.ollama_model}（已安装：{', '.join(models) or '无'}）",
                }
            client = OllamaLLM(
                base_url=cfg.ollama_base_url, model=cfg.ollama_model, timeout=30
            )
            resp = await run_in_threadpool(
                client.chat,
                [{"role": "user", "content": "请只回复：OK"}],
                max_tokens=16,
                temperature=0.0,
            )
            return {"ok": True, "message": f"本地模型可用：{cfg.ollama_model}", "reply": resp.content}

        if not cfg.api_key.strip():
            return {"ok": False, "message": "请先填写 API Key"}
        client = ApiLLMClient(
            api_key=cfg.api_key, base_url=cfg.base_url, model=cfg.model, timeout=30, max_retries=0
        )
        resp = await run_in_threadpool(
            client.chat,
            [{"role": "user", "content": "请只回复：OK"}],
            max_tokens=16,
            temperature=0.0,
        )
        return {"ok": True, "message": f"在线模型可用：{cfg.model}", "reply": resp.content}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": _error_text(exc)}


def _error_text(exc: Exception) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:300]
