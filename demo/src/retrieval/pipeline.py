"""检索统一入口。

对上层只暴露一个检索函数，策略可选纯向量、纯 BM25、混合检索与混合后精排，对应消融实验的四个档位。检索器实例按分块策略缓存，向量模型加载成本高，必须跨请求复用。各步耗时写入响应对象，也可追加到检索日志。精排模型不可用时退回混合检索结果并记录告警，不影响整条问答链路。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from config import settings
from src.retrieval.base import RetrievalResult, TimingRecorder, get_logger
from src.retrieval.bm25_retriever import BM25Retriever
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranker import Reranker
from src.retrieval.vector_retriever import VectorRetriever

logger = get_logger(__name__)

# 支持的检索策略
RetrievalStrategy = Literal["vector", "bm25", "hybrid", "hybrid_rerank"]

VALID_STRATEGIES: tuple[str, ...] = ("vector", "bm25", "hybrid", "hybrid_rerank")



@dataclass
class RetrievalResponse:
    """
    一次检索的完整结果。

    除文档块列表外还带上耗时、召回统计与所用参数，供评估与调试面板使用。
    """

    query: str
    results: list[RetrievalResult] = field(default_factory=list)
    strategy: str = ""
    chunk_strategy: str = ""
    timing: dict[str, float] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def __getitem__(self, index: int) -> RetrievalResult:
        return self.results[index]

    @property
    def texts(self) -> list[str]:
        return [r.text for r in self.results]

    @property
    def chunk_ids(self) -> list[str]:
        return [r.chunk_id for r in self.results]

    @property
    def top1(self) -> RetrievalResult | None:
        return self.results[0] if self.results else None

    def as_context(self) -> list[dict[str, Any]]:
        """转为组装指令模板所需的上下文结构。"""
        return [
            {
                "index": i,
                "chunk_id": r.chunk_id,
                "text": r.text,
                "score": r.final_score,
                "source": r.source,
                "page": r.page,
                "citation": r.citation(),
            }
            for i, r in enumerate(self.results, 1)
        ]

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        data = {
            "query": self.query,
            "strategy": self.strategy,
            "chunk_strategy": self.chunk_strategy,
            "count": len(self.results),
            "timing": self.timing,
            "stats": self.stats,
            "params": self.params,
            "warnings": self.warnings,
            "results": [
                (r.to_dict() if include_text else {k: v for k, v in r.to_dict().items() if k != "text"})
                for r in self.results
            ],
        }
        if not include_text:
            data["results"] = [
                {k: v for k, v in item.items() if k != "text"} for item in data["results"]
            ]
        return data

    def summary(self) -> str:
        t = self.timing
        parts = [f"{k}={v * 1000:.0f}ms" for k, v in t.items() if k != "total"]
        return (
            f"[{self.strategy}] {len(self.results)} 条 | "
            f"{' '.join(parts)} | 合计={t.get('total', 0) * 1000:.0f}ms"
            + (f" | 告警: {'; '.join(self.warnings)}" if self.warnings else "")
        )



def log_retrieval(response: RetrievalResponse, path: Path | None = None) -> Path:
    """
    把一次检索记录追加到 JSONL 文件。

    逐行追加，单条格式错误不会损坏整个文件，可在真实使用中记录各步耗时。
    """
    target = path or (settings.evaluation.result_dir / "retrieval_log.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "query": response.query,
        "strategy": response.strategy,
        "chunk_strategy": response.chunk_strategy,
        "count": len(response.results),
        "timing": response.timing,
        "stats": response.stats,
        "top_chunk_ids": response.chunk_ids,
        "top_scores": [round(r.final_score, 6) for r in response.results],
        "warnings": response.warnings,
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return target



def _preload_store_stack() -> None:
    """在旁路线程中预热索引侧依赖，与模型侧导入并行。

    chromadb 与 jieba 的导入和模型导入互不依赖，并行可缩短启动时间。预热失败不影主流程，真正用到时会重新导入并抛出原始错误。
    """
    for module in ("chromadb", "jieba"):
        try:
            __import__(module)
        except Exception as exc:  # noqa: BLE001 - 预热失败不应影响启动
            logger.debug("旁路预热 %s 失败: %s", module, exc)



class RetrievalPipeline:
    """
    检索管线，持有各检索器单例并按策略组装链路。

    通过模块级的全局获取函数取用，不要频繁构造本类。
    """

    def __init__(self, chunk_strategy: str | None = None) -> None:
        self.chunk_strategy = (chunk_strategy or settings.chunk.default_strategy).strip().lower()
        self._vector: VectorRetriever | None = None
        self._bm25: BM25Retriever | None = None
        self._hybrid: dict[tuple[str, float, float], HybridRetriever] = {}
        self._reranker: Reranker | None = None
    @property
    def vector(self) -> VectorRetriever:
        if self._vector is None:
            self._vector = VectorRetriever(self.chunk_strategy)
        return self._vector

    @property
    def bm25(self) -> BM25Retriever:
        if self._bm25 is None:
            self._bm25 = BM25Retriever(self.chunk_strategy)
        return self._bm25

    def get_hybrid(
        self,
        fusion: str | None = None,
        dense_weight: float | None = None,
        sparse_weight: float | None = None,
    ) -> HybridRetriever:
        """
        按融合方式与权重缓存混合检索器。

        权重是缓存键的一部分，不同权重各自对应一个实例，避免参数相互污染。
        """
        cfg = settings.retrieval
        key = (
            (fusion or cfg.fusion).lower(),
            cfg.dense_weight if dense_weight is None else dense_weight,
            cfg.sparse_weight if sparse_weight is None else sparse_weight,
        )
        if key not in self._hybrid:
            self._hybrid[key] = HybridRetriever(
                self.chunk_strategy,
                vector=self.vector,
                bm25=self.bm25,
                fusion=key[0],
                dense_weight=key[1],
                sparse_weight=key[2],
            )
        return self._hybrid[key]

    @property
    def reranker(self) -> Reranker:
        if self._reranker is None:
            self._reranker = Reranker()
        return self._reranker
    def warmup(self, strategies: tuple[str, ...] = VALID_STRATEGIES, *, include_reranker: bool = True) -> dict[str, float]:
        """
        预热所有检索器与精排模型，返回各自耗时。

        服务启动时调用，避免第一个真实请求承担模型加载延迟。模型导入与索引侧依赖并行执行以缩短启动时间。
        """
        used: dict[str, float] = {}
        t0 = time.perf_counter()
        preload = threading.Thread(
            target=_preload_store_stack, name="preload-store", daemon=True,
        )
        preload.start()
        try:
            if "vector" in strategies or "hybrid" in strategies or "hybrid_rerank" in strategies:
                used["vector"] = round(self.vector.warmup(), 3)
            if "bm25" in strategies or "hybrid" in strategies or "hybrid_rerank" in strategies:
                used["bm25"] = round(self.bm25.warmup(), 3)
            if include_reranker and "hybrid_rerank" in strategies:
                used["reranker"] = round(self.reranker.load(), 3)
        finally:
            preload.join()
        used["total"] = round(time.perf_counter() - t0, 3)
        logger.info("检索管线预热完成: %s", used)
        return used
    def retrieve(
        self,
        query: str,
        strategy: str = "hybrid_rerank",
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
        rerank_top_k: int | None = None,
        fusion: str | None = None,
        dense_weight: float | None = None,
        sparse_weight: float | None = None,
        write_log: bool | None = None,
    ) -> RetrievalResponse:
        """
        执行检索。

        查询文本为空时返回带告警的空结果。返回条数与融合候选池大小是两个独立参数：混合后精排策略的候选池固定取检索配置的条数，再从中挑出精排保留条数进入上下文；其余策略的返回条数即最终条数。日志写入开关默认取评估配置。
        """
        name = (strategy or "hybrid_rerank").strip().lower()
        if name not in VALID_STRATEGIES:
            raise ValueError(f"未知检索策略 '{strategy}'，可选：{' | '.join(VALID_STRATEGIES)}")

        if not query or not query.strip():
            return RetrievalResponse(
                query=query, strategy=name, chunk_strategy=self.chunk_strategy,
                warnings=["查询为空"],
            )

        query = query.strip()
        cfg = settings.retrieval
        cand = candidate_k or cfg.dense_top_k

        # 混合后精排时，返回条数表示精排保留条数，候选池仍取检索配置的条数；其余策略返回条数即最终条数
        rerank_k: int | None = None
        pool_k = top_k
        if name == "hybrid_rerank":
            rerank_k = rerank_top_k if rerank_top_k is not None else (top_k or settings.rerank.top_k)
            pool_k = cfg.top_k  # 候选池取检索配置的条数，供精排挑选

        warnings: list[str] = []
        timing = TimingRecorder()
        stats: dict[str, Any] = {}
        with timing.measure("recall"):
            if name == "vector":
                hits = self.vector.retrieve(query, top_k or cfg.top_k)
                stats["dense"] = len(hits)
                stats["sparse"] = 0
            elif name == "bm25":
                hits = self.bm25.retrieve(query, top_k or cfg.top_k)
                stats["dense"] = 0
                stats["sparse"] = len(hits)
            else:
                hybrid = self.get_hybrid(fusion, dense_weight, sparse_weight)
                outcome = hybrid.retrieve_detailed(query, pool_k or cfg.top_k, candidate_k=cand)
                hits = outcome.merged
                stats.update(outcome.stats())
        if name == "hybrid_rerank":
            with timing.measure("rerank"):
                try:
                    hits = self.reranker.rerank(query, hits, rerank_k)
                    stats["reranked"] = len(hits)
                except Exception as exc:  # noqa: BLE001 - 精排失败时保留混合检索结果
                    msg = f"精排模型不可用，已退回混合检索结果: {type(exc).__name__}: {exc}"
                    logger.warning(msg)
                    warnings.append(msg)
                    stats["reranked"] = 0

        response = RetrievalResponse(
            query=query,
            results=hits,
            strategy=name,
            chunk_strategy=self.chunk_strategy,
            timing=timing.as_dict(),
            stats=stats,
            params={
                "top_k": top_k or cfg.top_k,
                "candidate_k": cand,
                "rerank_top_k": rerank_k or settings.rerank.top_k,
                "fusion": fusion or cfg.fusion,
                "dense_weight": cfg.dense_weight if dense_weight is None else dense_weight,
                "sparse_weight": cfg.sparse_weight if sparse_weight is None else sparse_weight,
                "rerank_enabled": name == "hybrid_rerank",
            },
            warnings=warnings,
        )

        logger.info(
            "检索完成 [%s] query=%r 命中=%d %s",
            name, query[:40], len(hits), timing.summary(),
        )

        should_log = settings.evaluation.save_raw_records if write_log is None else write_log
        if should_log:
            try:
                log_retrieval(response)
            except Exception as exc:  # noqa: BLE001 - 写日志失败不影响检索
                logger.debug("写检索日志失败: %s", exc)

        return response

    def retrieve_batch(
        self,
        queries: list[str],
        strategy: str = "hybrid_rerank",
        *,
        show_progress: bool = True,
        write_log: bool = False,
        **kwargs: Any,
    ) -> list[RetrievalResponse]:
        """
        批量检索，评估时的核心循环。

        逐条调用而非批量处理，以便测出每条问题的真实延迟。
        """
        iterator: Any = queries
        if show_progress:
            from tqdm import tqdm

            iterator = tqdm(queries, desc=f"检索[{strategy}]", unit="问", ncols=90)
        return [
            self.retrieve(q, strategy, write_log=write_log, **kwargs) for q in iterator
        ]

    def info(self) -> dict[str, Any]:
        """管线状态，供健康检查与前端调试面板使用。"""
        return {
            "chunk_strategy": self.chunk_strategy,
            "vector": self.vector.info() if self._vector is not None else {"loaded": False},
            "bm25": self.bm25.info() if self._bm25 is not None else {"loaded": False},
            "reranker": self._reranker.info() if self._reranker is not None else {"loaded": False},
            "fusion": settings.retrieval.fusion,
            "weights": {
                "dense": settings.retrieval.dense_weight,
                "sparse": settings.retrieval.sparse_weight,
            },
        }



_PIPELINES: dict[str, RetrievalPipeline] = {}


def get_pipeline(chunk_strategy: str | None = None) -> RetrievalPipeline:
    """获取按分块策略缓存的全局检索管线。"""
    key = (chunk_strategy or settings.chunk.default_strategy).strip().lower()
    if key not in _PIPELINES:
        _PIPELINES[key] = RetrievalPipeline(key)
    return _PIPELINES[key]


def retrieve(
    query: str,
    strategy: str = "hybrid_rerank",
    chunk_strategy: str | None = None,
    **kwargs: Any,
) -> RetrievalResponse:
    """
    检索的统一对外入口。

    返回检索响应对象，其结果列表中的每个文档块都带元数据与得分。策略与分块策略均可指定，未指定时取配置值。
    """
    return get_pipeline(chunk_strategy).retrieve(query, strategy, **kwargs)


def warmup(*args: Any, **kwargs: Any) -> dict[str, float]:
    """预热默认管线。"""
    return get_pipeline().warmup(*args, **kwargs)


def pipeline_info() -> dict[str, Any]:
    """当前管线的状态信息。"""
    return get_pipeline().info()
