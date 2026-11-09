"""混合检索与融合。

向量与 BM25 各取若干条后融合成一个排序，融合方式可选仅向量、仅 BM25、倒数排名融合与加权分数融合。倒数排名融合只看名次，不用原始分数，因此分数绝对值很小，仅用于排序。加权融合先把两路分数归一化到同一量纲再求和。权重为 0 表示禁用该路。两路互相独立，用线程池并行发起，单路失败时降级为另一路结果。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Sequence

from config import settings
from src.retrieval.base import (
    BaseRetriever,
    RetrievalResult,
    TimingRecorder,
    get_logger,
    minmax_normalize,
)
from src.retrieval.bm25_retriever import BM25Retriever
from src.retrieval.vector_retriever import VectorRetriever

logger = get_logger(__name__)


class HybridRetriever(BaseRetriever):
    """
    混合检索器，向量与 BM25 并行召回后融合排序。

    普通检索返回融合结果，详细检索额外返回两路原始结果。
    """

    name = "hybrid"

    def __init__(
        self,
        strategy: str | None = None,
        *,
        vector: VectorRetriever | None = None,
        bm25: BM25Retriever | None = None,
        fusion: str | None = None,
        dense_weight: float | None = None,
        sparse_weight: float | None = None,
    ) -> None:
        super().__init__(strategy)
        cfg = settings.retrieval

        self.vector = vector or VectorRetriever(self.strategy)
        self.bm25 = bm25 or BM25Retriever(self.strategy)

        self.fusion = (fusion or cfg.fusion).strip().lower()
        if self.fusion not in ("dense", "sparse", "rrf", "weighted"):
            raise ValueError(f"未知融合策略 '{self.fusion}'，可选：dense | sparse | rrf | weighted")

        self.dense_weight = cfg.dense_weight if dense_weight is None else dense_weight
        self.sparse_weight = cfg.sparse_weight if sparse_weight is None else sparse_weight
        self.rrf_k = cfg.rrf_k
    def _load(self) -> None:
        # 按融合策略只加载需要的一路，避免无用加载
        if self.needs_dense:
            self.vector.warmup()
        if self.needs_sparse:
            self.bm25.warmup()
        logger.info(
            "HybridRetriever 就绪: 策略=%s 融合=%s 权重(dense=%.2f, sparse=%.2f) rrf_k=%d",
            self.strategy, self.fusion, self.dense_weight, self.sparse_weight, self.rrf_k,
        )

    def warmup(self) -> float:
        # 覆盖基类实现，把两路加载耗时都计入
        import time

        t0 = time.perf_counter()
        if not self._loaded:
            self._load()
            self._loaded = True
        self._load_seconds = time.perf_counter() - t0
        return self._load_seconds

    @property
    def needs_dense(self) -> bool:
        return self.fusion in ("dense", "rrf", "weighted") and self.dense_weight > 0

    @property
    def needs_sparse(self) -> bool:
        return self.fusion in ("sparse", "rrf", "weighted") and self.sparse_weight > 0

    @property
    def size(self) -> int:
        """可用文档块总数，两路都加载时取一致值，否则取已加载的一路。"""
        if self.vector._loaded:
            return self.vector.size
        if self.bm25._loaded:
            return self.bm25.size
        return 0

    @property
    def default_top_k(self) -> int:
        return settings.retrieval.top_k
    def _do_retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        return self._fuse_query(query, top_k).merged

    def retrieve_detailed(
        self,
        query: str,
        top_k: int | None = None,
        *,
        candidate_k: int | None = None,
    ) -> "FusionOutcome":
        """
        检索并返回完整过程数据，包括两路原始结果、融合结果与耗时。

        调试与评估需要查看正确文档是否被召回以及排在第几，只看融合结果不够。
        """
        if not query or not query.strip():
            return FusionOutcome(merged=[], dense=[], sparse=[], timing=TimingRecorder())
        if not self._loaded:
            self.warmup()
        k = top_k or self.default_top_k
        t0 = __import__("time").perf_counter()
        outcome = self._fuse_query(query.strip(), k, candidate_k=candidate_k)
        self.last_seconds = __import__("time").perf_counter() - t0
        return outcome
    def _recall(self, query: str, candidate_k: int, timing: TimingRecorder) -> tuple[list, list]:
        """并行发起两路召回，返回向量结果与 BM25 结果。"""
        want_dense, want_sparse = self.needs_dense, self.needs_sparse

        # 单路模式下无需启用线程池
        if want_dense and not want_sparse:
            with timing.measure("dense"):
                return self.vector.retrieve(query, candidate_k), []
        if want_sparse and not want_dense:
            with timing.measure("sparse"):
                return [], self.bm25.retrieve(query, candidate_k)

        dense: list[RetrievalResult] = []
        sparse: list[RetrievalResult] = []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="recall") as pool:
            f_dense = pool.submit(self.vector.retrieve, query, candidate_k) if want_dense else None
            f_sparse = pool.submit(self.bm25.retrieve, query, candidate_k) if want_sparse else None

            if f_dense is not None:
                t0 = __import__("time").perf_counter()
                try:
                    dense = f_dense.result()
                except Exception as exc:  # noqa: BLE001 - 单路失败降级而非整体失败
                    logger.warning("向量检索失败，将仅使用 BM25 结果: %s", exc)
                timing.record("dense", __import__("time").perf_counter() - t0)

            if f_sparse is not None:
                t0 = __import__("time").perf_counter()
                try:
                    sparse = f_sparse.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("BM25 检索失败，将仅使用向量结果: %s", exc)
                timing.record("sparse", __import__("time").perf_counter() - t0)

        return dense, sparse

    def _fuse_query(
        self,
        query: str,
        top_k: int,
        *,
        candidate_k: int | None = None,
    ) -> "FusionOutcome":
        timing = TimingRecorder()
        cand = candidate_k or max(self.vector.default_top_k, self.bm25.default_top_k)

        dense, sparse = self._recall(query, cand, timing)

        with timing.measure("fuse"):
            merged = self.fuse(dense, sparse, top_k=top_k)

        logger.debug(
            "混合检索: 候选=%d(dense=%d, sparse=%d) -> 融合=%d | %s",
            cand, len(dense), len(sparse), len(merged), timing.summary(),
        )
        return FusionOutcome(merged=merged, dense=dense, sparse=sparse, timing=timing)
    def _score_rrf(
        self,
        dense: Sequence[RetrievalResult],
        sparse: Sequence[RetrievalResult],
    ) -> dict[str, tuple[float, list[str]]]:
        """
        倒数排名融合，按名次倒数加权累加。

        分数绝对值很小属于正常现象，仅用于排序，不能与余弦相似度或 BM25 分数比较。
        """
        acc: dict[str, tuple[float, list[str]]] = {}
        for weight, hits in ((self.dense_weight, dense), (self.sparse_weight, sparse)):
            if weight <= 0:
                continue
            for item in hits:
                score, matched = acc.get(item.chunk_id, (0.0, []))
                acc[item.chunk_id] = (
                    score + weight / (self.rrf_k + item.rank),
                    matched + [item.retriever],
                )
        return acc

    def _score_weighted(
        self,
        dense: Sequence[RetrievalResult],
        sparse: Sequence[RetrievalResult],
    ) -> dict[str, tuple[float, list[str]]]:
        """
        加权分数融合，先把每路分数归一化，再按权重求和。

        归一化不可省略，否则数量级更大的 BM25 分数会主导融合结果。
        """
        acc: dict[str, tuple[float, list[str]]] = {}
        for weight, hits in ((self.dense_weight, dense), (self.sparse_weight, sparse)):
            if weight <= 0 or not hits:
                continue
            normed = minmax_normalize([h.score for h in hits])
            for item, norm in zip(hits, normed):
                score, matched = acc.get(item.chunk_id, (0.0, []))
                acc[item.chunk_id] = (score + weight * norm, matched + [item.retriever])
        return acc

    def fuse(
        self,
        dense: Sequence[RetrievalResult],
        sparse: Sequence[RetrievalResult],
        top_k: int,
    ) -> list[RetrievalResult]:
        """
        融合两路检索结果，返回前若干条。

        融合只依据分块 ID，两路同时召回的块把贡献相加，并记录召回来源以便解释排序。
        """
        if not dense and not sparse:
            return []

        if self.fusion == "dense":
            scored = {h.chunk_id: (h.score, [h.retriever]) for h in dense}
        elif self.fusion == "sparse":
            scored = {h.chunk_id: (h.score, [h.retriever]) for h in sparse}
        elif self.fusion == "rrf":
            scored = self._score_rrf(dense, sparse)
        else:  # 加权融合
            scored = self._score_weighted(dense, sparse)

        # 按分块 ID 建索引，融合后从任一路取原文与元数据
        pool: dict[str, RetrievalResult] = {}
        for item in list(sparse) + list(dense):  # 向量结果后写入，优先作为载体
            pool.setdefault(item.chunk_id, item)

        merged: list[RetrievalResult] = []
        for cid, (score, matched) in scored.items():
            base = pool.get(cid)
            if base is None:  # 理论不可达，防御性处理
                continue
            merged.append(
                RetrievalResult(
                    chunk_id=cid,
                    text=base.text,
                    score=score,
                    metadata=dict(base.metadata),
                    retriever=self.name,
                    rank=0,  # 融合排序后统一赋值
                    fused_score=score,
                    matched_by=sorted(set(matched)),
                )
            )

        # 按分数降序排列，同分时优先两路都召回的块
        merged.sort(key=lambda r: (-r.final_score, -len(r.matched_by)))
        merged = merged[:top_k]
        for i, item in enumerate(merged, 1):
            item.rank = i
        return merged
    def info(self) -> dict[str, Any]:
        data = super().info()
        data.update({
            "fusion": self.fusion,
            "dense_weight": self.dense_weight,
            "sparse_weight": self.sparse_weight,
            "rrf_k": self.rrf_k,
            "candidate_k": self.vector.default_top_k,
            "vector": self.vector.info(),
            "bm25": self.bm25.info(),
        })
        return data


class FusionOutcome:
    """一次混合检索的完整产物，便于调试与评估。"""

    __slots__ = ("merged", "dense", "sparse", "timing")

    def __init__(
        self,
        merged: list[RetrievalResult],
        dense: list[RetrievalResult],
        sparse: list[RetrievalResult],
        timing: TimingRecorder,
    ) -> None:
        self.merged = merged
        self.dense = dense
        self.sparse = sparse
        self.timing = timing

    def stats(self) -> dict[str, Any]:
        """两路召回的重叠情况，用于衡量混合检索的互补性。"""
        d_ids = {h.chunk_id for h in self.dense}
        s_ids = {h.chunk_id for h in self.sparse}
        return {
            "dense": len(d_ids),
            "sparse": len(s_ids),
            "overlap": len(d_ids & s_ids),
            "dense_only": len(d_ids - s_ids),
            "sparse_only": len(s_ids - d_ids),
            "merged": len(self.merged),
            "timing": self.timing.as_dict(),
        }
