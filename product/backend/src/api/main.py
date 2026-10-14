"""FastAPI 主入口（main.py）。

- 无认证：不注册 /auth，不加鉴权中间件。
- 启动即可响应：Embedding / Rerank 预热放在**后台线程**，/health 用 ``warmed_up`` 反映进度，
  预热期间也能正常请求（只是首个真实请求会自行加载模型）。
- 请求日志中间件用**纯 ASGI 中间件**实现，避免 BaseHTTPMiddleware 缓冲流式响应（会破坏 SSE）。
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from src.core.config import settings
from src.core.logging import get_logger, new_request_id, request_id_var, setup_logging
from src.api.v1 import api_router
from src.db import models  # noqa: F401 - 导入以注册所有 ORM 模型

setup_logging()
logger = get_logger(__name__)

# ------------------------------- 指标 --------------------------------------- #
REQUESTS_TOTAL = Counter("rag_requests_total", "HTTP 请求数", ["method", "status"])
REQUEST_LATENCY = Histogram(
    "rag_request_latency_seconds", "HTTP 请求耗时（秒）", ["method", "status"]
)


# ------------------------------ 预热状态 ------------------------------------ #
class _WarmupState:
    def __init__(self) -> None:
        self.started = False
        self.done = False
        self.detail: dict = {}
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        return {"warmed_up": self.done, "warming_up": self.started and not self.done,
                "detail": self.detail}


_state = _WarmupState()


def _warmup_background() -> None:
    """后台线程预热检索与精排模型（幂等，只跑一次）。"""
    with _state.lock:
        if _state.started:
            return
        _state.started = True
    try:
        from src.services.retrieval_service import warmup_all

        _state.detail = warmup_all(include_reranker=True)
        _state.done = True
        logger.info("后台预热完成: %s", _state.detail)
    except Exception as exc:  # noqa: BLE001 - 预热失败不影响服务
        _state.detail = {"error": f"{type(exc).__name__}: {exc}"}
        logger.warning("后台预热失败: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    logger.info("服务启动 | %s", settings.summary())
    if settings.api.warmup_on_startup:
        threading.Thread(target=_warmup_background, name="model-warmup", daemon=True).start()
    yield
    logger.info("服务关闭")


# ------------------------------ ASGI 中间件 --------------------------------- #
class RequestLogMiddleware:
    """记录请求日志与指标；纯 ASGI 实现，不缓冲响应体（流式 SSE 安全）。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = new_request_id()
        token = request_id_var.set(rid)
        method = scope.get("method", "-")
        path = scope.get("path", "-")
        start = time.perf_counter()
        holder = {"status": 500}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                holder["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            status = str(holder["status"])
            REQUESTS_TOTAL.labels(method=method, status=status).inc()
            REQUEST_LATENCY.labels(method=method, status=status).observe(elapsed)
            logger.info("%s %s -> %s %.0fms", method, path, status, elapsed * 1000)
            request_id_var.reset(token)


# -------------------------------- 应用 -------------------------------------- #
app = FastAPI(
    title="基于混合检索与Rerank的领域知识库系统",
    description="多知识库领域问答：BM25 + 向量双路召回，RRF 融合，交叉编码器精排。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"name": app.title, "version": app.version, "docs": "/docs"}


@app.get("/health", summary="健康检查")
async def health() -> dict:
    from src.utils.redis_client import ping as redis_ping

    return {
        "status": "ok",
        "domain": settings.domain,
        "warmed_up": _state.done,
        "warming_up": _state.started and not _state.done,
        "redis": redis_ping(),
        "warmup_detail": _state.detail,
    }


# 前端以 /api/v1 为 baseURL，健康检查调用 /api/v1/health；
# 这里复用同一处理函数，避免前端判定「后端未连接」。
app.add_api_route(
    f"{settings.api.api_prefix}/health",
    health,
    methods=["GET"],
    summary="健康检查（与 /health 等价）",
    include_in_schema=False,
)


@app.post("/warmup", summary="手动触发热身（幂等）")
async def warmup() -> dict:
    if not _state.started:
        threading.Thread(target=_warmup_background, name="model-warmup", daemon=True).start()
    return _state.snapshot()


@app.get("/config", summary="当前生效配置（密钥脱敏）")
async def config() -> dict:
    return settings.to_dict(hide_secrets=True)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常 %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": f"服务器内部错误: {type(exc).__name__}"})


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=settings.api.reload,
        log_level=settings.api.log_level.lower(),
    )
