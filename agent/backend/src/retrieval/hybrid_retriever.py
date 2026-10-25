"""混合检索与融合（hybrid_retriever.py）。

向量与 BM25 各取 Top-K 后融合成一个排序。``retrieval.fusion`` 四选一：
``dense`` / ``sparse``（消融基线）、``rrf``（默认，``score = Σ w_i / (k + rank_i)``，k 默认 60）、
``weighted``（两路分数各自 min-max 归一化后加权求和）。

RRF 只按名次、不用原始分数：向量余弦约 0.3~0.9、BM25 可达 0~20+，量纲差异巨大，直接加权
会被 BM25 单方面决定；RRF 分数绝对值很小（两路命中约 0.032）是正常的，仅用于排序，
不要与余弦相似度或 BM25 分数比大小。``weighted`` 必须先 min-max 归一化，否则同样由 BM25 决定。

权重在融合层生效：设为 0 等价于禁用该路。权重不改变单路内部名次，但会改变两路之间的交叉
关系（例如 0.5/0.5 时 D 可排在 C 前，1.0/0.0 时相反），这是消融实验的预期行为，不是 bug。

两路互相独立（Chroma + 内存 BM25），用线程池并行发起；单路失败自动降级为另一路结果。

适配多知识库：向量、BM25 子检索器均按 ``kb_id`` 加载对应索引。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Sequence

from src.core.config import settings
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
    混合检索器：向量 + BM25 并行召回，再融合排序。

    ``retrieve()`` 返回融合结果，``retrieve_detailed()`` 额外带上两路原始结果。
    """

    name = "hybrid"

    def __init__(
        self,
        kb_id: int,
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
        self.kb_id = int(kb_id)

        self.vector = vector or VectorRetriever(self.kb_id, self.strategy)
        self.bm25 = bm25 or BM25Retriever(self.kb_id, self.strategy)

        self.fusion = (fusion or cfg.fusion).strip().lower()
        if self.fusion not in ("dense", "sparse", "rrf", "weighted"):
            raise ValueError(f"未知融合策略 '{self.fusion}'，可选：dense | sparse | rrf | weighted")

        self.dense_weight = cfg.dense_weight if dense_weight is None else dense_weight
        self.sparse_weight = cfg.sparse_weight if sparse_weight is None else sparse_weight
        self.rrf_k = cfg.rrf_k
    def _load(self) -> None:
        # 按融合策略只加载需要的那一路，避免无谓读索引/加载模型
        if self.needs_dense:
            self.vector.warmup()
        if self.needs_sparse:
            self.bm25.warmup()
        logger.info(
            "HybridRetriever 就绪: kb=%d 策略=%s 融合=%s 权重(dense=%.2f, sparse=%.2f) rrf_k=%d",
            self.kb_id, self.strategy, self.fusion, self.dense_weight, self.sparse_weight, self.rrf_k,
        )

    def warmup(self) -> float:
        # 覆盖基类实现，以便把两路加载耗时都算进去
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
        """可用文档块总数：两路都加载时取一致值，否则取已加载那一路。"""
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
        检索并返回完整过程数据（两路原始结果 + 融合结果 + 耗时）。

        调试面板与消融/错误分析要看「正确文档有没有被召回、排第几」，
        只看融合结果不够；正常问答路径用 ``retrieve()`` 即可。
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
        """并行发起两路召回，返回 (向量结果, BM25 结果)。"""
        want_dense, want_sparse = self.needs_dense, self.needs_sparse

        # dense/sparse 单路模式：不必起线程
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
        倒数排名融合：``score(chunk) = Σ w_i / (k + rank_i)``

        分数绝对值极小是正常的 —— RRF 只用于排序，不要与余弦相似度或 BM25 分数比大小。
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
        加权分数融合：先把每路分数 min-max 归一化，再按权重求和。

        归一化是必需的：BM25 分数可达 20+，余弦相似度只有 0~1，
        不归一化直接加权等于把融合结果交给 BM25 单方面决定。
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
        融合两路检索结果，返回 Top-k。

        融合只依据 ``chunk_id``（两个索引的 chunk_id 一致），无需按文本相似度去重；
        两路同时召回的块把两路贡献相加，并记录 ``matched_by`` 以便解释排序。
        """
        if not dense and not sparse:
            return []

        if self.fusion == "dense":
            scored = {h.chunk_id: (h.score, [h.retriever]) for h in dense}
        elif self.fusion == "sparse":
            scored = {h.chunk_id: (h.score, [h.retriever]) for h in sparse}
        elif self.fusion == "rrf":
            scored = self._score_rrf(dense, sparse)
        else:  # weighted
            scored = self._score_weighted(dense, sparse)

        # 用 chunk_id 建索引，融合后从任一路取原文与元数据
        pool: dict[str, RetrievalResult] = {}
        for item in list(sparse) + list(dense):  # dense 后写入，优先保留向量结果作为载体
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
                    rank=0,  # 稍后按融合排序统一赋值
                    fused_score=score,
                    matched_by=sorted(set(matched)),
                )
            )

        # 分数降序；同分时优先"两路都召回"的块，再按原 rank 稳定排序
        merged.sort(key=lambda r: (-r.final_score, -len(r.matched_by)))
        merged = merged[:top_k]
        for i, item in enumerate(merged, 1):
            item.rank = i
        return merged
    def info(self) -> dict[str, Any]:
        data = super().info()
        data.update({
            "kb_id": self.kb_id,
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
    """一次混合检索的完整产物，便于调试。"""

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
        """两路召回的重叠情况 —— 混合检索有效性的直接证据。"""
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
