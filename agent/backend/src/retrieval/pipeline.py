"""检索统一入口（pipeline.py）。

对上层（对话、API、检索服务）只暴露 ``retrieve()``：按**构建块开关**组装链路——
``vector`` 纯向量、``bm25`` 纯词匹配、``hybrid`` 双路 RRF 融合、``hybrid_rerank`` 融合后再
交叉编码器精排（完整链路），四种开关组合可还原 demo 的四个档位。

开关（``strategies``）为 ``{"vector","bm25","fusion","rerank"}``，默认全 true：
- 只开 vector → 纯向量；只开 bm25 → 纯 BM25；vector+bm25(+fusion) → 混合(RRF)；再加 rerank → 混合+精排
- 非法组合（两路召回都开但 fusion=false）→ 按融合执行，并在 ``RetrievalResponse.strategy``
  记录**实际生效**的策略名（``vector`` | ``bm25`` | ``hybrid`` | ``hybrid_rerank``）

检索器实例按 ``(kb_id, 分块策略)`` 缓存：向量模型加载成本高（约 2.3 GB / 7~15 秒），必须跨请求复用。
精排模型不可用时降级为混合检索结果并告警，而不是让整条问答链路 500。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from src.core.config import settings
from src.retrieval.base import RetrievalResult, TimingRecorder, get_logger
from src.retrieval.bm25_retriever import BM25Retriever
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranker import Reranker
from src.retrieval.vector_retriever import VectorRetriever

logger = get_logger(__name__)

#: 支持的检索策略名（四选一，用于记录实际生效策略）
RetrievalStrategy = Literal["vector", "bm25", "hybrid", "hybrid_rerank"]

VALID_STRATEGIES: tuple[str, ...] = ("vector", "bm25", "hybrid", "hybrid_rerank")

#: 构建块开关默认值：全开
DEFAULT_STRATEGIES: dict[str, bool] = {"vector": True, "bm25": True, "fusion": True, "rerank": True}



@dataclass
class RetrievalResponse:
    """
    一次检索的完整结果。

    除最终文档块列表外还带上耗时、召回统计与所用参数，供 API 调试面板使用。
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
        """转为生成侧组装 Prompt 所需的上下文结构。"""
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



def normalize_strategies(strategies: dict[str, bool] | None) -> dict[str, bool]:
    """归一化构建块开关：缺省字段按全开处理，只保留已知键。"""
    out = dict(DEFAULT_STRATEGIES)
    if strategies:
        for key in DEFAULT_STRATEGIES:
            if key in strategies:
                out[key] = bool(strategies[key])
    return out



class RetrievalPipeline:
    """
    检索管线：持有各检索器的单例，按构建块开关组装链路。

    不要频繁构造本类 —— 用模块级 ``get_pipeline()`` 拿（按 kb 缓存的）全局实例。
    """

    def __init__(self, kb_id: int, chunk_strategy: str | None = None) -> None:
        self.kb_id = int(kb_id)
        self.chunk_strategy = (chunk_strategy or settings.chunk.default_strategy).strip().lower()
        self._vector: VectorRetriever | None = None
        self._bm25: BM25Retriever | None = None
        self._hybrid: dict[tuple[str, float, float], HybridRetriever] = {}
        self._reranker: Reranker | None = None
    @property
    def vector(self) -> VectorRetriever:
        if self._vector is None:
            self._vector = VectorRetriever(self.kb_id, self.chunk_strategy)
        return self._vector

    @property
    def bm25(self) -> BM25Retriever:
        if self._bm25 is None:
            self._bm25 = BM25Retriever(self.kb_id, self.chunk_strategy)
        return self._bm25

    def get_hybrid(
        self,
        fusion: str | None = None,
        dense_weight: float | None = None,
        sparse_weight: float | None = None,
    ) -> HybridRetriever:
        """
        按 (融合方式, 权重) 缓存混合检索器。

        权重是缓存键的一部分：调参会用不同权重反复检索，只缓存一个实例会让上一次的参数污染本次。
        """
        cfg = settings.retrieval
        key = (
            (fusion or cfg.fusion).lower(),
            cfg.dense_weight if dense_weight is None else dense_weight,
            cfg.sparse_weight if sparse_weight is None else sparse_weight,
        )
        if key not in self._hybrid:
            self._hybrid[key] = HybridRetriever(
                self.kb_id,
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
    def warmup(self, *, include_reranker: bool = True) -> dict[str, float]:
        """
        预热检索器与精排模型，返回各自耗时。

        FastAPI 启动时调用，避免第一个真实请求承担约 20 秒的加载延迟。
        """
        used: dict[str, float] = {}
        t0 = time.perf_counter()
        used["vector"] = round(self.vector.warmup(), 3)
        used["bm25"] = round(self.bm25.warmup(), 3)
        if include_reranker and settings.rerank.enabled:
            used["reranker"] = round(self.reranker.load(), 3)
        used["total"] = round(time.perf_counter() - t0, 3)
        logger.info("检索管线预热完成: kb=%d %s", self.kb_id, used)
        return used
    def retrieve(
        self,
        query: str,
        strategies: dict[str, bool] | None = None,
        *,
        top_k: int | None = None,
    ) -> RetrievalResponse:
        """
        执行检索。

        :param query: 查询文本
        :param strategies: 构建块开关 ``{"vector","bm25","fusion","rerank"}``，默认全 true
        :param top_k: 覆盖最终返回条数（``hybrid_rerank`` 下等价于精排保留条数）；不传取配置值
        :returns: RetrievalResponse，``strategy`` 为**实际生效**的策略名
        """
        sw = normalize_strategies(strategies)
        cfg = settings.retrieval
        warnings: list[str] = []

        if not query or not query.strip():
            return RetrievalResponse(
                query=query, strategy="", chunk_strategy=self.chunk_strategy,
                warnings=["查询为空"],
            )
        query = query.strip()

        vec_on = sw["vector"]
        bm_on = sw["bm25"]
        fusion_on = sw["fusion"]
        rerank_on = sw["rerank"]

        # 两路召回都关 → 无召回路径，按双路混合执行
        if not vec_on and not bm_on:
            warnings.append("未启用任何召回路径，已按双路混合检索执行")
            vec_on = bm_on = True

        # 组装实际链路：base ∈ {vector, bm25, hybrid}
        if vec_on and bm_on:
            base = "hybrid"
            if not fusion_on:
                warnings.append("双路召回均开启但未启用融合，已按 RRF 融合执行")
        elif vec_on:
            base = "vector"
        else:
            base = "bm25"

        cand = cfg.dense_top_k
        timing = TimingRecorder()
        stats: dict[str, Any] = {}
        with timing.measure("recall"):
            if base == "vector":
                hits = self.vector.retrieve(query, top_k or cfg.top_k)
                stats["dense"] = len(hits)
                stats["sparse"] = 0
            elif base == "bm25":
                hits = self.bm25.retrieve(query, top_k or cfg.top_k)
                stats["dense"] = 0
                stats["sparse"] = len(hits)
            else:
                hybrid = self.get_hybrid()
                pool_k = cfg.top_k  # 融合候选池固定为 retrieval.top_k（默认 20）
                outcome = hybrid.retrieve_detailed(query, pool_k, candidate_k=cand)
                hits = outcome.merged
                stats.update(outcome.stats())

        rerank_applied = False
        if rerank_on and hits:
            rerank_k = top_k or settings.rerank.top_k
            with timing.measure("rerank"):
                try:
                    hits = self.reranker.rerank(query, hits, rerank_k)
                    stats["reranked"] = len(hits)
                    rerank_applied = True
                except Exception as exc:  # noqa: BLE001 - 精排失败不应让整条链路失败
                    msg = f"精排模型不可用，已退回检索结果: {type(exc).__name__}: {exc}"
                    logger.warning(msg)
                    warnings.append(msg)
                    stats["reranked"] = 0

        # 记录实际生效策略名：hybrid 基座 + 精排 → hybrid_rerank；否则为召回基座
        strategy = "hybrid_rerank" if (base == "hybrid" and rerank_applied) else base

        response = RetrievalResponse(
            query=query,
            results=hits,
            strategy=strategy,
            chunk_strategy=self.chunk_strategy,
            timing=timing.as_dict(),
            stats=stats,
            params={
                "top_k": top_k or cfg.top_k,
                "candidate_k": cand,
                "rerank_top_k": settings.rerank.top_k,
                "fusion": cfg.fusion,
                "dense_weight": cfg.dense_weight,
                "sparse_weight": cfg.sparse_weight,
                "rrf_k": cfg.rrf_k,
                "rerank_enabled": rerank_applied,
                "strategies": {
                    "vector": vec_on, "bm25": bm_on,
                    "fusion": bool(fusion_on), "rerank": bool(rerank_on),
                },
            },
            warnings=warnings,
        )

        logger.info(
            "检索完成 kb=%d [%s] query=%r 命中=%d %s",
            self.kb_id, strategy, query[:40], len(hits), timing.summary(),
        )
        return response

    def info(self) -> dict[str, Any]:
        """管线状态，供 API 的 /health 与前端调试面板使用。"""
        return {
            "kb_id": self.kb_id,
            "chunk_strategy": self.chunk_strategy,
            "vector": self.vector.info() if self._vector is not None else {"loaded": False},
            "bm25": self.bm25.info() if self._bm25 is not None else {"loaded": False},
            "reranker": self._reranker.info() if self._reranker is not None else {"loaded": False},
            "fusion": settings.retrieval.fusion,
            "timing_params": {
                "dense_top_k": settings.retrieval.dense_top_k,
                "sparse_top_k": settings.retrieval.sparse_top_k,
                "fusion_top_k": settings.retrieval.top_k,
                "rrf_k": settings.retrieval.rrf_k,
                "rerank_top_k": settings.rerank.top_k,
                "context_max_chars": settings.generation.context_max_chars,
            },
            "default_strategies": dict(DEFAULT_STRATEGIES),
        }



#: 按 (kb_id, 分块策略) 缓存的全局管线
_PIPELINES: dict[tuple[int, str], RetrievalPipeline] = {}


def get_pipeline(kb_id: int, chunk_strategy: str | None = None) -> RetrievalPipeline:
    """获取（并按 (kb_id, 分块策略) 缓存的）全局检索管线。"""
    key = (int(kb_id), (chunk_strategy or settings.chunk.default_strategy).strip().lower())
    if key not in _PIPELINES:
        _PIPELINES[key] = RetrievalPipeline(key[0], key[1])
    return _PIPELINES[key]


def clear_pipelines() -> None:
    """清空管线缓存（知识库索引变更后调用，使下次检索重新加载）。"""
    _PIPELINES.clear()
