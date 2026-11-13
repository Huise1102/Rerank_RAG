import asyncio
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.api.router import api_router
from src.api.routes.health import router as health_router
from src.core.config import settings
from src.core.logging import configure_logging, request_id_var

logger = logging.getLogger(__name__)


def _preload_models() -> None:
    from src.retrieval.retrievers import preload_models

    try:
        preload_models()
        logger.info("嵌入与精排模型预加载完成")
    except Exception as exc:
        logger.warning("检索模型预加载失败：%s", exc)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.on_event("startup")
    async def prepare_directories() -> None:
        for path in (
            settings.data_path,
            settings.storage_path,
            settings.chroma_path,
            settings.bm25_path,
            settings.logs_path,
            settings.hf_cache_path,
        ):
            path.mkdir(parents=True, exist_ok=True)
        logger.info("服务启动，配置加载完成")
        asyncio.create_task(asyncio.to_thread(_preload_models))

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
