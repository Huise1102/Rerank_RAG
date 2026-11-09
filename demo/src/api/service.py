"""业务编排层，把检索、生成、引用解析与响应组装串成一条链路供路由调用。

检索器必须是单例，向量模型与精排模型都占用大量内存；预热把首次请求的模型加载提前到启动阶段；重建索引后旧检索器仍指向旧集合，必须失效缓存才能重新加载。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from config import settings
from src.api.schemas import (
    Citation,
    CitationReport,
    LatencyBreakdown,
    QueryRequest,
    QueryResponse,
    RetrievedDoc,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)



def _to_doc(item: Any) -> RetrievedDoc:
    """把检索结果对象转为接口模型。"""
    return RetrievedDoc(
        rank=int(getattr(item, "rank", 0) or 0),
        chunk_id=str(getattr(item, "chunk_id", "")),
        text=str(getattr(item, "text", "")),
        source=str(getattr(item, "source", "")),
        page=getattr(item, "page", ""),
        score=float(getattr(item, "final_score", 0.0) or 0.0),
        fused_score=getattr(item, "fused_score", None),
        rerank_score=getattr(item, "rerank_score", None),
        retriever=str(getattr(item, "retriever", "")),
        matched_by=list(getattr(item, "matched_by", []) or []),
    )


def _to_citation_report(report: Any, *, include_text: bool = True) -> CitationReport:
    """把生成模块的引用报告转为接口模型。"""
    citations = [
        Citation(
            index=c.index,
            source=c.source,
            page=c.page,
            chunk_id=c.chunk_id,
            text=c.text if include_text else "",
            label=c.label(),
            count=c.count,
            score=c.score,
        )
        for c in report.citations
    ]
    return CitationReport(
        citations=citations,
        unused_indices=[c.index for c in report.unused_contexts],
        invalid_indices=list(report.invalid_indices),
        is_refusal=bool(report.is_refusal),
        coverage=round(float(report.coverage), 4),
        summary=report.summary(),
    )



@dataclass
class WarmupState:
    """预热结果，供健康检查展示。"""

    done: bool = False
    seconds: float = 0.0
    detail: dict[str, float] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "done": self.done,
            "seconds": round(self.seconds, 3),
            "detail": self.detail,
            "error": self.error,
        }


class RagService:
    """
    RAG 服务门面。

    问答无状态可并发；预热与缓存失效会替换共享对象，因此加锁。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pipeline = None
        self._llm = None
        self._warmup = WarmupState()
    def pipeline_for(self, chunk_strategy: str | None = None):
        """取指定分块策略的检索管线。

        分块策略属于管线级配置，决定读取哪个集合与哪个 BM25 文件；请求带分块策略时必须切换管线实例，不能把该参数透传给检索方法。未指定时使用默认管线。
        """
        from src.retrieval.pipeline import get_pipeline

        if chunk_strategy:
            return get_pipeline(chunk_strategy)
        if self._pipeline is None:
            self._pipeline = get_pipeline()
        return self._pipeline

    @property
    def pipeline(self):
        """默认分块策略的检索管线，按策略缓存在检索模块内部。"""
        return self.pipeline_for(None)

    @property
    def llm(self):
        """大模型客户端，全局单例。"""
        from src.generation.llm import get_llm_client

        if self._llm is None:
            self._llm = get_llm_client()
        return self._llm

    @property
    def warmup_state(self) -> WarmupState:
        return self._warmup
    def warmup(self, *, include_reranker: bool = True) -> WarmupState:
        """预热检索管线，加载向量模型、精排模型与 BM25 索引；重复调用返回上次结果。"""
        with self._lock:
            if self._warmup.done:
                return self._warmup

            if not settings.rerank.enabled:
                include_reranker = False

            t0 = time.perf_counter()
            logger.info("开始预热检索管线（精排=%s）…", include_reranker)
            try:
                detail = self.pipeline.warmup(include_reranker=include_reranker)
                self._warmup = WarmupState(
                    done=True,
                    seconds=time.perf_counter() - t0,
                    detail={k: float(v) for k, v in detail.items()},
                )
                logger.info(
                    "预热完成，总耗时 %.1fs：%s", self._warmup.seconds, self._warmup.detail
                )
            except Exception as exc:  # noqa: BLE001 - 预热失败不影响服务启动
                self._warmup = WarmupState(
                    done=False, seconds=time.perf_counter() - t0,
                    error=f"{type(exc).__name__}: {exc}",
                )
                logger.error("预热失败（服务仍会启动，首个请求会较慢）: %s", exc)
            return self._warmup

    def reload(self) -> None:
        """
        索引变更后失效缓存，使下次查询重新加载。

        向量检索器持有集合句柄，BM25 检索器持有加载后的对象，两者都不会自动感知磁盘变化。
        """
        with self._lock:
            from src.retrieval import pipeline as pipeline_module

            logger.info("索引已变更，失效检索器缓存以便重新加载")
            self._pipeline = None
            pipeline_module._PIPELINES.clear()
            # 向量库与 BM25 都已更换，必须重新预热，否则首个请求仍要等待模型加载
            self._warmup = WarmupState()
    def query(self, request: QueryRequest) -> QueryResponse:
        """
        执行一次完整问答，依次检索、生成、解析引用并组装响应。
        """
        from src.generation.llm import generate

        t_start = time.perf_counter()
        warnings: list[str] = []
        t0 = time.perf_counter()
        pipeline = self.pipeline_for(request.chunk_strategy)
        retrieval = pipeline.retrieve(
            request.question,
            strategy=request.strategy,
            top_k=request.top_k,
            write_log=False,
        )
        retrieve_ms = (time.perf_counter() - t0) * 1000
        warnings.extend(retrieval.warnings)

        timing = retrieval.timing or {}
        recall_ms = float(timing.get("recall", timing.get("total", 0.0))) * 1000
        rerank_ms = float(timing.get("rerank", 0.0)) * 1000
        t0 = time.perf_counter()
        llm_response = generate(request.question, retrieval.results)
        generate_ms = (time.perf_counter() - t0) * 1000
        warnings.extend(llm_response.warnings)
        report = llm_response.citations
        docs = [_to_doc(item) for item in retrieval.results]
        if not request.include_contexts:
            # 只保留溯源必需字段，长度超过阈值的正文置空以减小响应体积
            docs = [
                d.model_copy(update={"text": ""}) if len(d.text) > 120 else d for d in docs
            ]

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "问答完成: strategy=%s 检索=%.0fms 生成=%.0fms 合计=%.0fms 引用=%d条",
            request.strategy, retrieve_ms, generate_ms, total_ms, len(report.citations),
        )

        return QueryResponse(
            answer=llm_response.answer,
            citations=_to_citation_report(report, include_text=request.include_contexts),
            retrieved_docs=docs,
            latency=LatencyBreakdown(
                retrieve_ms=round(retrieve_ms, 1),
                recall_ms=round(recall_ms, 1),
                rerank_ms=round(rerank_ms, 1),
                generate_ms=round(generate_ms, 1),
                total_ms=round(total_ms, 1),
            ),
            strategy=request.strategy,
            chunk_strategy=retrieval.chunk_strategy,
            model=llm_response.model,
            usage=llm_response.usage or {},
            warnings=warnings,
        )
    def retrieve_only(
        self,
        question: str,
        strategy: str = "hybrid_rerank",
        chunk_strategy: str | None = None,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        """
        只检索不生成，供前端检索调试面板展示两路召回的重叠情况。
        """
        result = self.pipeline_for(chunk_strategy).retrieve(
            question, strategy=strategy, top_k=top_k, write_log=False,
        )
        return {
            "question": question,
            "strategy": result.strategy,
            "chunk_strategy": result.chunk_strategy,
            "docs": [_to_doc(item) for item in result.results],
            "stats": dict(result.stats or {}),
            "timing": {k: round(float(v), 4) for k, v in (result.timing or {}).items()},
            "warnings": list(result.warnings),
        }
    def stream_query(self, request: QueryRequest):
        """
        流式问答，返回事件迭代器与结果容器。

        事件类型依次为元信息，含检索结果与耗时；答案增量；完成，含完整引用与总耗时；以及错误。迭代结束后容器中的响应对象为完整结果，便于记录日志或落库。
        """
        t_start = time.perf_counter()
        retrieval = self.pipeline_for(request.chunk_strategy).retrieve(
            request.question, strategy=request.strategy,
            top_k=request.top_k, write_log=False,
        )
        retrieve_ms = (time.perf_counter() - t_start) * 1000
        timing = retrieval.timing or {}
        docs = [_to_doc(item) for item in retrieval.results]
        box: dict[str, Any] = {"response": None}

        def _events():
            yield {
                "type": "meta",
                "strategy": retrieval.strategy,
                "chunk_strategy": retrieval.chunk_strategy,
                "retrieved_docs": [d.model_dump() for d in docs],
                "stats": dict(retrieval.stats or {}),
                "latency": {
                    "retrieve_ms": round(retrieve_ms, 1),
                    "recall_ms": round(float(timing.get("recall", 0.0)) * 1000, 1),
                    "rerank_ms": round(float(timing.get("rerank", 0.0)) * 1000, 1),
                },
                "warnings": list(retrieval.warnings),
            }

            t_gen = time.perf_counter()
            stream, inner = self.llm.stream_response(request.question, retrieval.results)
            pieces: list[str] = []
            try:
                for delta in stream:
                    pieces.append(delta)
                    yield {"type": "delta", "delta": delta}
            except Exception as exc:  # noqa: BLE001 - 流式中断也要给出收尾
                logger.warning("流式生成异常: %s", exc)
                yield {"type": "error", "error": f"{type(exc).__name__}: {exc}"}

            generate_ms = (time.perf_counter() - t_gen) * 1000
            llm_response = inner.get("response")
            answer = "".join(pieces).strip() or (
                llm_response.answer if llm_response else settings.generation.no_context_reply
            )
            report = llm_response.citations if llm_response else None
            citations = (
                _to_citation_report(report, include_text=request.include_contexts)
                if report is not None else CitationReport()
            )

            total_ms = (time.perf_counter() - t_start) * 1000
            response = QueryResponse(
                answer=answer,
                citations=citations,
                retrieved_docs=docs,
                latency=LatencyBreakdown(
                    retrieve_ms=round(retrieve_ms, 1),
                    recall_ms=round(float(timing.get("recall", 0.0)) * 1000, 1),
                    rerank_ms=round(float(timing.get("rerank", 0.0)) * 1000, 1),
                    generate_ms=round(generate_ms, 1),
                    total_ms=round(total_ms, 1),
                    first_token_ms=(
                        round(llm_response.first_token_seconds * 1000, 1)
                        if llm_response and llm_response.first_token_seconds else None
                    ),
                ),
                strategy=request.strategy,
                chunk_strategy=retrieval.chunk_strategy,
                model=llm_response.model if llm_response else "",
                usage=(llm_response.usage if llm_response else {}) or {},
                warnings=list(retrieval.warnings) + (
                    list(llm_response.warnings) if llm_response else []
                ),
            )
            box["response"] = response
            logger.info(
                "流式问答完成: strategy=%s 检索=%.0fms 生成=%.0fms 合计=%.0fms",
                request.strategy, retrieve_ms, generate_ms, total_ms,
            )
            yield {"type": "done", "response": response.model_dump()}

        return _events(), box
    def health_components(self) -> list[dict[str, Any]]:
        """各组件就绪状态，任一组件不可用都不应让健康检查直接报错。"""
        components: list[dict[str, Any]] = []

        # 大模型
        try:
            ready, detail = self.llm.is_ready()
            components.append({"name": "llm", "ready": bool(ready), "detail": detail})
        except Exception as exc:  # noqa: BLE001
            components.append({"name": "llm", "ready": False,
                               "detail": f"{type(exc).__name__}: {exc}"})

        # 向量库
        try:
            from src.ingest.indexer import collection_name_for_strategy, get_chroma_client

            coll = collection_name_for_strategy(settings.chunk.default_strategy)
            count = get_chroma_client().get_collection(coll).count()
            components.append({
                "name": "vector_store", "ready": count > 0,
                "detail": f"{coll}: {count} 条",
            })
        except Exception as exc:  # noqa: BLE001
            components.append({"name": "vector_store", "ready": False,
                               "detail": f"{type(exc).__name__}: {exc}"})

        # 稀疏检索
        try:
            from src.ingest.indexer import bm25_path, load_bm25_index

            path = bm25_path(settings.chunk.default_strategy)
            count = len(load_bm25_index(settings.chunk.default_strategy)) if path.exists() else 0
            components.append({
                "name": "bm25", "ready": count > 0,
                "detail": f"{path.name}: {count} 条",
            })
        except Exception as exc:  # noqa: BLE001
            components.append({"name": "bm25", "ready": False,
                               "detail": f"{type(exc).__name__}: {exc}"})

        # 精排
        state = self._warmup
        if settings.rerank.enabled:
            reranker = getattr(self._pipeline, "_reranker", None) if self._pipeline else None
            loaded = bool(reranker is not None and getattr(reranker, "loaded", False))
            components.append({
                "name": "reranker", "ready": loaded or state.done,
                "detail": (
                    f"{settings.rerank.resolved_model}"
                    + (f"（预热 {state.detail.get('reranker', 0):.1f}s）" if loaded else "（未预热）")
                ),
            })

        return components

    def index_status(self) -> dict[str, Any]:
        """当前分块策略的索引现状，包含一致性检查。"""
        from src.ingest.indexer import index_stats

        strategy = settings.chunk.default_strategy
        try:
            info = index_stats(strategy)
            counts = (info.get("chunks", 0), info.get("vector_count", 0), info.get("bm25_count", 0))
            return {
                "chunk_strategy": strategy,
                "chunks_in_file": int(counts[0]),
                "vector_count": int(counts[1]),
                "bm25_count": int(counts[2]),
                "collection": str(info.get("collection", "")),
                # 三项计数不一致说明索引与分块文件不同步，例如入库中途失败
                "consistent": len(set(counts)) == 1 and counts[0] > 0,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取索引状态失败: %s", exc)
            return {
                "chunk_strategy": strategy, "chunks_in_file": 0, "vector_count": 0,
                "bm25_count": 0, "collection": "", "consistent": False,
            }


_SERVICE: RagService | None = None
_SERVICE_LOCK = threading.Lock()


def get_service() -> RagService:
    """获取全局 RAG 服务单例。"""
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = RagService()
    return _SERVICE
