"""FastAPI 应用：/health、/query、/query/stream、/retrieve、/ingest、/strategies、/config。
启动：``python -m src.api.main``；路径同时挂在 ``/api/v1`` 前缀下，完整文档见 ``/docs``。

两个关键决策：预热必须放后台线程（同步等待会让服务在模型加载期间连 /health 都不响应）；
检索/生成是阻塞操作，路由用普通 ``def`` 交给线程池，声明 ``async def`` 会阻塞事件循环。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator

# 允许 `python -m src.api.main` 直接运行
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from config import PROJECT_ROOT, settings
from src.api import ingest as ingest_module
from src.api.schemas import (
    DEFAULT_STRATEGY,
    ChunkStrategyName,
    ComponentStatus,
    HealthResponse,
    IndexStatus,
    IngestResponse,
    IngestStatus,
    QueryRequest,
    QueryResponse,
    RetrieveRequest,
    RetrieveResponse,
    StrategiesResponse,
    StrategyOption,
    StrategyName,
)
from src.api.service import get_service
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)

__version__ = "0.4.0"

#: 检索策略的展示信息（前端下拉框直接用这份）
RETRIEVAL_STRATEGIES: list[dict[str, str]] = [
    {"value": "vector", "label": "向量检索（仅语义）",
     "description": "BGE 稠密向量检索，擅长语义相近但用词不同的表达"},
    {"value": "bm25", "label": "BM25（仅关键词）",
     "description": "词频检索，对专有名词、文号、条款号等精确匹配最有效，速度极快"},
    {"value": "hybrid", "label": "混合检索（向量 + BM25 + RRF）",
     "description": "两路并行召回后用倒数排名融合，兼顾语义与精确匹配"},
    {"value": "hybrid_rerank", "label": "混合检索 + Rerank 精排（推荐）",
     "description": "完整链路：融合后交给交叉编码器精排，相关性最高"},
]

CHUNK_STRATEGIES: list[dict[str, str]] = [
    {"value": "fixed", "label": "固定长度分块（500/50）",
     "description": "按字符窗口滑动切分，块长均匀"},
    {"value": "recursive", "label": "递归语义分块（推荐）",
     "description": "优先在段落与句子边界切分，语义完整性更好"},
]



@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子。"""
    setup_logging()
    logger.info("=" * 70)
    logger.info("RAG 问答系统 API 启动中…")
    logger.info("配置: %s", settings.summary())
    logger.info("文档: http://%s:%d/docs", settings.api.host, settings.api.port)
    logger.info("=" * 70)

    service = get_service()
    app.state.service = service
    app.state.warmup_thread = None

    if settings.api.warmup_on_startup:
        # 后台线程预热：不阻塞启动，/health 可查询进度
        def _warmup() -> None:
            try:
                service.warmup()
            except Exception as exc:  # noqa: BLE001
                logger.error("后台预热异常: %s", exc)

        thread = threading.Thread(target=_warmup, name="warmup", daemon=True)
        thread.start()
        app.state.warmup_thread = thread
        logger.info("已在后台线程启动预热（服务当前即可响应 /health）")

    yield

    logger.info("API 服务关闭。")



def _service(request: Request):
    """取服务单例（测试时可通过 app.state 替换）。"""
    return getattr(request.app.state, "service", None) or get_service()


def _resolve_strategy(request: Request, body_strategy: str | None = None) -> str:
    """
    解析检索策略：``?strategy=bm25`` 与请求体 ``strategy`` 都支持，两者都给时以请求体为准。
    """
    from_query = request.query_params.get("strategy")
    chosen = body_strategy or from_query or DEFAULT_STRATEGY
    if chosen not in {s["value"] for s in RETRIEVAL_STRATEGIES}:
        raise HTTPException(
            status_code=422,
            detail=f"未知检索策略 '{chosen}'，可选：{[s['value'] for s in RETRIEVAL_STRATEGIES]}",
        )
    return chosen


router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["系统"],
            summary="健康检查")
def health(request: Request) -> HealthResponse:
    """返回服务与各依赖组件状态。不加载模型，因此预热未完成时也能立即返回。"""
    service = _service(request)
    raw_components = service.health_components()
    components = [
        ComponentStatus(name=c["name"], ready=bool(c["ready"]), detail=str(c.get("detail", "")))
        for c in raw_components
    ]

    state = service.warmup_state
    try:
        index_info = service.index_status()
        index = IndexStatus(**index_info)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取索引状态失败: %s", exc)
        index = None

    # 判定整体状态：检索与生成可用即算 ok
    critical = {"llm", "vector_store", "bm25"}
    not_ready = {c.name for c in components if not c.ready}
    if not not_ready:
        status = "ok"
    elif critical & not_ready:
        status = "error"
    else:
        status = "degraded"

    return HealthResponse(
        status=status,
        components=components,
        index=index,
        warmed_up=state.done,
        warmup_seconds=round(state.seconds, 3),
        version=__version__,
    )


@router.post("/warmup", tags=["系统"], summary="手动触发预热（幂等）")
def warmup(request: Request) -> dict[str, Any]:
    """显式预热。已预热时立即返回上次结果。"""
    service = _service(request)
    state = service.warmup()
    return state.to_dict()


@router.post("/query", response_model=QueryResponse, tags=["问答"],
             summary="问答：检索 + 生成（非流式）")
def query(request: Request, payload: QueryRequest | None = None) -> QueryResponse:
    """
    执行一次完整问答。请求体可省略，此时从查询参数取 ``question``/``strategy``；
    两者同时给策略时以请求体为准。示例::

        POST /query   {"question": "学校规章制度的适用范围是怎么规定的？", "strategy": "hybrid_rerank"}
    """
    body = payload
    if body is None:
        # 无请求体：从查询参数构造（便于浏览器/curl 直接调）
        question = request.query_params.get("question")
        if not question or not question.strip():
            raise HTTPException(
                status_code=422,
                detail="缺少问题：请提供 JSON 请求体 {\"question\": \"...\"} 或 question 查询参数",
            )
        chunk_strategy = request.query_params.get("chunk_strategy")
        if chunk_strategy is not None and chunk_strategy not in ("fixed", "recursive"):
            raise HTTPException(
                status_code=422, detail=f"未知分块策略 '{chunk_strategy}'，可选：fixed | recursive"
            )
        body = QueryRequest(
            question=question,
            strategy=_resolve_strategy(request),  # type: ignore[arg-type]
            chunk_strategy=chunk_strategy,  # type: ignore[arg-type]
        )
    else:
        strategy = _resolve_strategy(request, body.strategy)
        if strategy != body.strategy:
            body = body.model_copy(update={"strategy": strategy})

    service = _service(request)
    try:
        return service.query(body)
    except HTTPException:
        raise
    except RuntimeError as exc:
        # LLM 未配置或全部模型调用失败：可预期的运维问题，返回 503 并带原因
        logger.error("问答失败: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/query/stream", tags=["问答"], summary="问答：流式（SSE）")
def query_stream(payload: QueryRequest, request: Request):
    """
    流式问答，返回 ``text/event-stream``；每条事件为一行 ``data: {json}``，
    ``type`` 依次为 ``meta``（检索结果与耗时）、``delta``（答案增量）、
    ``done``（完整 QueryResponse）、``error``。
    """
    service = _service(request)
    strategy = _resolve_strategy(request, payload.strategy)
    if strategy != payload.strategy:
        payload = payload.model_copy(update={"strategy": strategy})

    events, box = service.stream_query(payload)

    def _sse() -> Iterator[str]:
        try:
            for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - 生成器内部异常也要通知前端
            logger.exception("SSE 流异常")
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关闭 nginx 缓冲，否则流式响应会被攒成一坨再发（部署到反代后必须）
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/retrieve", response_model=RetrieveResponse, tags=["检索"],
             summary="仅检索（返回两路召回明细）")
def retrieve(payload: RetrieveRequest, request: Request) -> RetrieveResponse:
    """
    只检索不生成。``stats`` 含 dense/sparse/overlap/dense_only/sparse_only，
    供前端调试面板展示两路各召回多少、多少是重叠的。
    """
    service = _service(request)
    strategy = _resolve_strategy(request, payload.strategy)
    data = service.retrieve_only(
        payload.question, strategy=strategy,
        chunk_strategy=payload.chunk_strategy, top_k=payload.top_k,
    )
    return RetrieveResponse(**data)


@router.get("/strategies", response_model=StrategiesResponse, tags=["系统"],
            summary="可选策略列表")
def strategies() -> StrategiesResponse:
    return StrategiesResponse(
        retrieval=[StrategyOption(**s) for s in RETRIEVAL_STRATEGIES],
        chunk=[StrategyOption(**s) for s in CHUNK_STRATEGIES],
        default_retrieval=DEFAULT_STRATEGY,
        default_chunk=settings.chunk.default_strategy,
    )


@router.get("/config", tags=["系统"], summary="当前生效配置（密钥已脱敏）")
def get_config() -> dict[str, Any]:
    """返回脱敏后的配置。``to_dict()`` 会把 API Key 替换为 ``sk-abc...xyz``。"""
    data = settings.to_dict(hide_secrets=True)
    data["project_root"] = str(PROJECT_ROOT)
    data["version"] = __version__
    return data



@router.post("/ingest", response_model=IngestResponse, tags=["入库"],
             summary="上传 PDF 触发增量入库")
async def ingest(
    request: Request,
    file: UploadFile | None = File(default=None, description="待入库的 PDF（可多次调用逐个上传）"),
    files: list[UploadFile] | None = File(default=None, description="批量上传"),
    strategy: ChunkStrategyName | None = Form(default=None),
    force: bool = Form(default=False, description="忽略文件指纹，强制重新解析所有文件"),
) -> IngestResponse:
    """
    上传 PDF 触发**增量**入库：立即返回 ``task_id``，用
    ``GET /ingest/status/{task_id}`` 轮询进度（向量化在 CPU 上需数分钟）。

    参数名兼容两种写法：单文件 ``file``，多文件 ``files``（可重复）。
    """
    uploads: list[UploadFile] = []
    if file is not None:
        uploads.append(file)
    if files:
        uploads.extend(files)
    if not uploads:
        raise HTTPException(status_code=422, detail="未收到文件，请用 file 或 files 字段上传 PDF")

    manager = ingest_module.get_ingest_manager()
    if manager.busy:
        raise HTTPException(
            status_code=409,
            detail="已有入库任务正在执行，请等待完成后再提交（避免并发修改索引）",
        )

    accepted: list[str] = []
    saved_paths: list[Path] = []
    rejected: list[str] = []

    for upload in uploads:
        try:
            data = await upload.read()
            path, conflict = ingest_module.save_upload(data, upload.filename or "upload.pdf")
        except ValueError as exc:
            rejected.append(f"{upload.filename}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            rejected.append(f"{upload.filename}: {type(exc).__name__}: {exc}")
            continue

        # conflict 三种取值必须显式区分：""=新文件保存成功；"identical"=内容完全一致
        # （幂等，不算保存、不触发重建）；其它字符串=同名但内容不同，拒绝并要求改名。
        if conflict == "identical":
            accepted.append(path.name)
            continue
        if conflict:
            rejected.append(conflict)
            continue
        accepted.append(path.name)
        saved_paths.append(path)

    if rejected and not accepted:
        raise HTTPException(status_code=422, detail="; ".join(rejected))

    if not saved_paths:
        # 没有新落盘文件：内容一致时幂等返回；但 force=true 必须真的强制重灌，否则
        # "强制重新解析"在上传同名同内容文件时会静默失效
        if not force:
            return IngestResponse(
                task_id="", status="done", accepted_files=accepted,
                message="文件与已有内容完全一致，无需重新入库（幂等）。"
                        "如需强制重灌，请勾选/传入 force=true",
            )
        manager_check = ingest_module.get_ingest_manager()
        if manager_check.busy:
            raise HTTPException(
                status_code=409,
                detail="已有入库任务正在执行，请等待完成后再提交",
            )

    # 入库范围是 raw 目录下全部 PDF：清单里未变的会被跳过，但索引必须包含所有文件，故提交整个目录。
    all_pdfs = sorted(settings.paths.raw_dir.rglob("*.pdf"))
    try:
        task = manager.submit(all_pdfs, strategy=strategy, force=force)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    message = f"已接收 {len(accepted)} 个文件，入库任务已在后台启动"
    if rejected:
        message += f"；{len(rejected)} 个被拒绝：" + "; ".join(rejected)

    return IngestResponse(
        task_id=task.task_id,
        status=task.status,  # type: ignore[arg-type]
        accepted_files=accepted,
        message=message,
        status_url=f"{settings.api.api_prefix}/ingest/status/{task.task_id}",
    )


@router.delete("/ingest/files/{file_name}", tags=["入库"], summary="删除已入库文件并重建索引")
def delete_ingested_file(file_name: str) -> IngestResponse:
    """
    从语料中移除一个文件并重建索引，重建同样走后台任务。

    上传错文件时需要一个"撤销"手段，否则只能手动删文件，容易漏掉清单与索引的同步。
    """
    from src.api.ingest import sanitize_filename

    manager = ingest_module.get_ingest_manager()
    if manager.busy:
        raise HTTPException(status_code=409, detail="已有入库任务正在执行，请等待完成后再删除")

    safe = sanitize_filename(file_name)
    target = settings.paths.raw_dir / safe
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"语料目录中不存在文件「{safe}」")

    target.unlink()
    logger.info("已从语料目录删除文件: %s", safe)

    # 同步从清单中移除，否则下次入库会认为它"仍需处理"却找不到文件
    manifest = ingest_module.load_manifest()
    manifest.get("files", {}).pop(safe, None)
    ingest_module.save_manifest(manifest)

    remaining = sorted(settings.paths.raw_dir.rglob("*.pdf"))
    if not remaining:
        return IngestResponse(
            task_id="", status="done", accepted_files=[safe],
            message=f"已删除「{safe}」。语料目录已空，索引未重建（无内容可建）",
        )

    task = manager.submit(remaining, strategy=None, force=False)
    return IngestResponse(
        task_id=task.task_id,
        status=task.status,  # type: ignore[arg-type]
        accepted_files=[safe],
        message=f"已删除「{safe}」，正在后台重建索引（剩余 {len(remaining)} 个文件）",
        status_url=f"{settings.api.api_prefix}/ingest/status/{task.task_id}",
    )


@router.get("/ingest/status/{task_id}", response_model=IngestStatus, tags=["入库"],
            summary="查询入库进度")
def ingest_status(task_id: str) -> IngestStatus:
    task = ingest_module.get_ingest_manager().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"未找到任务 {task_id}（可能已过期）")
    return IngestStatus(**task.to_dict())


@router.get("/ingest/tasks", tags=["入库"], summary="最近若干入库任务")
def ingest_tasks(limit: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
    manager = ingest_module.get_ingest_manager()
    return {"busy": manager.busy, "tasks": manager.list_recent(limit)}


@router.get("/ingest/manifest", tags=["入库"], summary="查看入库清单（已入库文件与指纹）")
def ingest_manifest() -> dict[str, Any]:
    data = ingest_module.load_manifest()
    files = data.get("files", {})
    return {
        "count": len(files),
        "updated_at": data.get("updated_at", ""),
        "files": [
            {
                "file_name": name,
                "pages": rec.get("pages", 0),
                "chunks": rec.get("chunks", 0),
                "indexed_at": rec.get("indexed_at", ""),
            }
            for name, rec in sorted(files.items())
        ],
    }



def create_app(*, warmup: bool | None = None) -> FastAPI:
    """
    创建 FastAPI 应用。

    :param warmup: 覆盖 ``settings.api.warmup_on_startup``；测试传 False 可跳过模型加载。
    """
    if warmup is not None:
        object.__setattr__(settings.api, "warmup_on_startup", warmup)

    app = FastAPI(
        title="RAG 领域问答系统 API",
        description=(
            "基于混合检索与 Rerank 的领域问答服务。\n\n"
            "- 检索链路：BGE 稠密向量 + BM25 稀疏检索 → RRF 融合 → 交叉编码器精排\n"
            "- 生成：DeepSeek（OpenAI 兼容接口），答案带 [编号] 引用并可溯源到页码"
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS：Streamlit 默认跑在 8501
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    if settings.api.mount_versioned_prefix:
        app.include_router(router, prefix=settings.api.api_prefix)

    @app.get("/", include_in_schema=False)
    def index() -> dict[str, Any]:
        return {
            "service": "RAG 领域问答系统 API",
            "version": __version__,
            "docs": "/docs",
            "endpoints": [
                "/health", "/query", "/query/stream", "/retrieve",
                "/ingest", "/ingest/status/{task_id}", "/strategies", "/config",
            ],
            "note": "以上路径同时挂载在 " + settings.api.api_prefix + " 前缀下",
        }

    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        """把业务层的 ValueError 统一转成 422，避免暴露 500 堆栈给前端。"""
        logger.warning("请求处理出现 ValueError: %s", exc)
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


#: uvicorn / gunicorn 的入口对象
app = create_app()


def main() -> int:
    """命令行启动：python -m src.api.main"""
    import uvicorn

    setup_logging()
    cfg = settings.api
    logger.info("启动 uvicorn: http://%s:%d", cfg.host, cfg.port)
    uvicorn.run(
        "src.api.main:app",
        host=cfg.host,
        port=cfg.port,
        reload=cfg.reload,
        log_level=cfg.log_level.lower(),
        # access 日志统一走我们的 logger
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
