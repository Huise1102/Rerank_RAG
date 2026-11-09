"""稀疏 BM25 检索器。

加载入库侧落盘的 BM25 索引，把查询用 jieba 分词后检索，返回前若干文档块及得分。

稠密检索擅长语义泛化，但对专有名词、编号、型号容易失灵，BM25 的精确词匹配补上这块短板。
rank_bm25 会把负 IDF 截断成小正数，导致高频停用词反而加分，因此需要停用词表与
文档频率上界双重过滤。
BM25 索引只保存词频、不保存原文，pickle 中同时存原文与分词结果，两者必须一一对应，
因此复用索引模块的加载函数，由它自带长度一致性校验。

索引按知识库隔离，从对应的索引文件加载。
"""

from __future__ import annotations

from typing import Any, Sequence

from src.core.config import settings
from src.retrieval.base import BaseRetriever, RetrievalResult, get_logger

logger = get_logger(__name__)


class BM25Retriever(BaseRetriever):
    """
    基于 rank_bm25 与 jieba 的稀疏检索器，按知识库隔离。
    """

    name = "bm25"

    def __init__(self, kb_id: int, strategy: str | None = None, *, index: Any = None) -> None:
        super().__init__(strategy)
        self.kb_id = int(kb_id)
        self._index = index
        self._shared_index = index is not None
    def _load(self) -> None:
        from src.ingest.indexer import bm25_path_for_kb, load_bm25_index_kb

        if self._index is None:
            try:
                self._index = load_bm25_index_kb(self.kb_id, self.strategy)
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"{exc}\n"
                    f"提示：BM25 索引与知识库一一对应，若刚换过分块策略请重新入库。"
                ) from exc
        logger.info(
            "BM25Retriever 就绪: kb=%d 文件=%s 条数=%d 分词器=%s",
            self.kb_id, bm25_path_for_kb(self.kb_id, self.strategy).name,
            len(self._index), settings.retrieval.bm25_tokenizer,
        )

    @property
    def size(self) -> int:
        return len(self._index) if self._index is not None else 0

    @property
    def default_top_k(self) -> int:
        return settings.retrieval.sparse_top_k
    def _do_retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        # BM25Index.search 内部完成 jieba 分词与打分，返回 [(语料下标, 分数)]
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
        返回查询对所有语料的原始 BM25 分数，用于消融与错误分析。

        用过滤后的有效查询词打分，与检索接口口径一致，否则得分会包含停用词引入的噪声。
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
        """解释一次 BM25 检索：哪些查询词被保留/剔除，为什么。"""
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
            "kb_id": self.kb_id,
            "tokenizer": settings.retrieval.bm25_tokenizer,
            "k1": settings.retrieval.bm25_k1,
            "b": settings.retrieval.bm25_b,
            "built_at": (self._index.meta.get("built_at") if self._index is not None else None),
        })
        return data
