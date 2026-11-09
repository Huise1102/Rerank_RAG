"""稀疏 BM25 检索器。

加载入库阶段落盘的 BM25 索引，查询经 jieba 分词后检索，返回前若干条文档块及得分。稠密检索对专有名词与编号容易失灵，BM25 的精确词匹配可作补充。rank_bm25 会把负 IDF 截断成小正数，因此需要停用词表与文档频率上界共同过滤。索引对象只保存词频不保存原文，加载时通过入库模块的加载函数获取，以保证原文与分词结果一一对应。
"""

from __future__ import annotations

from typing import Any, Sequence

from config import settings
from src.retrieval.base import BaseRetriever, RetrievalResult, get_logger

logger = get_logger(__name__)


class BM25Retriever(BaseRetriever):
    """
    基于 rank_bm25 与 jieba 的稀疏检索器。
    """

    name = "bm25"

    def __init__(self, strategy: str | None = None, *, index: Any = None) -> None:
        super().__init__(strategy)
        self._index = index
        self._shared_index = index is not None
    def _load(self) -> None:
        from src.ingest.indexer import bm25_path, load_bm25_index

        if self._index is None:
            try:
                self._index = load_bm25_index(self.strategy)
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"{exc}\n"
                    f"提示：BM25 索引与分块策略一一对应，若刚换过策略请重新入库。"
                ) from exc
        logger.info(
            "BM25Retriever 就绪: 文件=%s 条数=%d 分词器=%s",
            bm25_path(self.strategy).name, len(self._index), settings.retrieval.bm25_tokenizer,
        )

    @property
    def size(self) -> int:
        return len(self._index) if self._index is not None else 0

    @property
    def default_top_k(self) -> int:
        return settings.retrieval.sparse_top_k
    def _do_retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        # 索引内部完成分词与打分，返回语料下标与分数的二元组
        hits: Sequence[tuple[int, float]] = self._index.search(query, top_k=top_k)

        out: list[RetrievalResult] = []
        for idx, score in hits:
            doc = self._index.documents[idx]
            meta = dict(doc.get("metadata", {}))
            out.append(
                RetrievalResult(
                    chunk_id=str(meta.get("chunk_id") or f"bm25:{idx}"),
                    text=str(doc.get("text", "")),
                    score=float(score),
                    metadata=meta,
                )
            )
        return out

    def score_all(self, query: str) -> list[float]:
        """
        返回查询对所有语料的原始 BM25 分数。

        只用过滤后的有效查询词打分，与检索接口口径一致，避免停用词引入噪声。
        """
        if not self._loaded:
            self.warmup()
        import numpy as np

        terms = self._index.informative_terms(query)
        if not terms:
            return [0.0] * len(self._index.documents)
        scores = self._index.bm25.get_scores(terms)
        return np.asarray(scores, dtype=float).tolist()

    def explain(self, query: str) -> dict[str, Any]:
        """解释一次 BM25 检索，说明哪些查询词被保留或剔除及原因。"""
        if not self._loaded:
            self.warmup()
        from src.ingest.indexer import _STOPWORDS, tokenize

        tokens = tokenize(query)
        terms = self._index.informative_terms(query)
        df = self._index._df_map()
        n = max(len(self._index.documents), 1)

        def reason_of(term: str) -> str:
            if term in _STOPWORDS:
                return "停用词"
            if df.get(term, 0) < settings.retrieval.bm25_min_term_df:
                return "未在语料中出现"
            return "过于普遍（df 超过上限）"

        return {
            "query": query,
            "tokens": tokens,
            "kept": terms,
            "dropped": [
                {"term": t, "df": df.get(t, 0), "reason": reason_of(t)}
                for t in tokens
                if t not in terms
            ],
            "df_ratio_limit": settings.retrieval.bm25_max_df_ratio,
            "corpus_size": n,
        }

    def info(self) -> dict[str, Any]:
        data = super().info()
        data.update({
            "tokenizer": settings.retrieval.bm25_tokenizer,
            "k1": settings.retrieval.bm25_k1,
            "b": settings.retrieval.bm25_b,
            "built_at": (self._index.meta.get("built_at") if self._index is not None else None),
        })
        return data
