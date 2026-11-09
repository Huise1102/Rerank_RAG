"""FastAPI 应用，提供健康检查、问答、流式问答、检索、入库、策略与配置等接口，路径同时挂载在版本前缀下。

预热放在后台线程，避免服务在模型加载期间连健康检查都无法响应；检索与生成都是阻塞操作，路由使用普通同步函数交给线程池执行，若声明为异步函数会阻塞事件循环。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator

# 允许以模块方式直接运行本文件
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

# 检索策略的展示信息，前端下拉框直接使用
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
    """启动与关闭钩子。"""
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
        # 后台线程预热，不阻塞启动，健康检查可查询进度
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
    """获取服务单例，测试时可通过应用状态替换。"""
    return getattr(request.app.state, "service", None) or get_service()


def _resolve_strategy(request: Request, body_strategy: str | None = None) -> str:
    """
    解析检索策略，查询参数与请求体均可指定，两者都给出时以请求体为准。
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
    """返回服务与各依赖组件状态。不加载模型，预热未完成时也能立即返回。"""
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

    # 整体状态判定：检索与生成可用即视为正常
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
    """显式预热，已预热时立即返回上次结果。"""
    service = _service(request)
    state = service.warmup()
    return state.to_dict()


@router.post("/query", response_model=QueryResponse, tags=["问答"],
             summary="问答：检索 + 生成（非流式）")
def query(request: Request, payload: QueryRequest | None = None) -> QueryResponse:
    """
    执行一次完整问答。请求体可省略，此时从查询参数取问题与策略；两者同时给出策略时以请求体为准。
    """
    body = payload
    if body is None:
        # 无请求体时从查询参数构造，便于用浏览器或命令行直接调用
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
        # 大模型未配置或全部模型调用失败属于可预期的运维问题，返回服务不可用并带上原因
        logger.error("问答失败: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/query/stream", tags=["问答"], summary="问答：流式（SSE）")
def query_stream(payload: QueryRequest, request: Request):
    """
    流式问答，返回服务端推送事件流，每条事件为一行数据。

    事件类型依次为元信息，含检索结果与耗时；答案增量；完成，含完整响应；以及错误。
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
            # 关闭反向代理缓冲，否则流式响应会被攒成一批再发送
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/retrieve", response_model=RetrieveResponse, tags=["检索"],
             summary="仅检索（返回两路召回明细）")
def retrieve(payload: RetrieveRequest, request: Request) -> RetrieveResponse:
    """
    只检索不生成。

    统计字段包含两路各召回多少条、重叠多少条以及仅由单路召回的条数，供前端调试面板展示。
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
    """返回脱敏后的配置，API Key 会被替换为只保留头尾的形式。"""
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
    上传 PDF 触发增量入库，立即返回任务编号，通过状态接口轮询进度。

    单文件与多文件两种参数名都支持。
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

        # 冲突结果分三类：空串表示新文件保存成功；内容一致表示重复上传，直接复用；其他字符串表示同名但内容不同，拒绝并提示改名
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
        # 没有新落盘文件时，内容一致可幂等返回；但强制重新入库必须真正重灌，否则上传同名同内容文件时强制选项会静默失效
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

    # 入库范围是原始目录下全部 PDF：清单中未变化的文件会被跳过，但索引必须覆盖所有文件，因此提交整个目录
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

    上传错文件时提供撤销手段，避免手动删文件后清单与索引不同步。
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

    # 同步从清单中移除，否则下次入库会认为该文件仍需处理却找不到文件
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
    创建应用。

    预热参数可覆盖配置中的启动预热开关，测试时传假值可跳过模型加载。
    """
    if warmup is not None:
        object.__setattr__(settings.api, "warmup_on_startup", warmup)

    app = FastAPI(
        title="领域知识库系统 API",
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

    # 跨域配置，前端开发服务器端口与后端不同
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
            "service": "领域知识库系统 API",
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
        """把业务层抛出的值错误统一转为 422，避免把 500 堆栈暴露给前端。"""
        logger.warning("请求处理出现 ValueError: %s", exc)
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


# 服务进程的入口对象
app = create_app()


def main() -> int:
    """命令行启动入口。"""
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
        # 访问日志统一交给本项目的日志器输出
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
