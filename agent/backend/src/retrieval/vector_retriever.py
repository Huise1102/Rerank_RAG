"""稠密向量检索器。

用 bge-large-zh-v1.5 把查询编码成向量，在 Chroma 中做余弦检索，返回前若干条文档块及得分。

查询侧加 BGE 检索指令前缀，文档侧不加，两侧保持这种不对称处理以提升召回。
Chroma 的余弦空间返回距离，直接按距离排序会得到相反顺序，
因此显式转成相似度，使分数越大越相关的约定在全系统一致，融合层无需再关心方向。

按知识库 ID 与分块策略加载对应集合。
"""

from __future__ import annotations

from typing import Any, Sequence

from src.core.config import settings
from src.retrieval.base import BaseRetriever, RetrievalResult, get_logger

logger = get_logger(__name__)


class VectorRetriever(BaseRetriever):
    """
    基于 Chroma 与 BGE 的稠密检索器，按知识库隔离。
    """

    name = "vector"

    def __init__(self, kb_id: int, strategy: str | None = None, *, embedder: Any = None) -> None:
        super().__init__(strategy)
        self.kb_id = int(kb_id)
        self._collection = None
        self._embedder = embedder
        self._device = "unknown"
        self._shared_embedder = embedder is not None
    def _load(self) -> None:
        from src.ingest.indexer import collection_name_for_kb, get_chroma_client

        coll_name = collection_name_for_kb(self.kb_id, self.strategy)
        client = get_chroma_client()
        try:
            self._collection = client.get_collection(coll_name)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"未找到 Chroma 集合 '{coll_name}'（知识库 {self.kb_id}，策略 {self.strategy}）。\n"
                f"请先完成该知识库的文档入库。\n"
                f"原始错误: {exc}"
            ) from exc

        if self._embedder is None:
            self._embedder, self._device = self._load_embedder()
        logger.info(
            "VectorRetriever 就绪: kb=%d 集合=%s 条数=%d 模型=%s",
            self.kb_id, coll_name, self._collection.count(), settings.embedding.resolved_model,
        )

    @staticmethod
    def _load_embedder():
        """复用入库侧的加载逻辑（含离线模式与预检），避免重复实现。"""
        from src.ingest.indexer import load_embedder

        return load_embedder()

    @property
    def size(self) -> int:
        return int(self._collection.count()) if self._collection is not None else 0

    @property
    def collection_name(self) -> str:
        return self._collection.name if self._collection is not None else ""

    @property
    def default_top_k(self) -> int:
        return settings.retrieval.dense_top_k
    def encode_query(self, query: str) -> list[float]:
        """把查询编码为归一化向量；按配置加上 BGE 检索指令前缀。"""
        cfg = settings.embedding
        text = f"{cfg.query_instruction}{query}" if cfg.use_query_instruction else query
        vector = self._embedder.encode(
            [text],
            normalize_embeddings=cfg.normalize_embeddings,
            convert_to_numpy=True,
            show_progress_bar=False,
        )[0]
        return vector.tolist()

    def encode_queries(self, queries: Sequence[str]) -> list[list[float]]:
        """批量编码（一次编码多条问题，比逐条快得多）。"""
        cfg = settings.embedding
        texts = [
            f"{cfg.query_instruction}{q}" if cfg.use_query_instruction else q
            for q in queries
        ]
        vectors = self._embedder.encode(
            texts,
            batch_size=cfg.batch_size,
            normalize_embeddings=cfg.normalize_embeddings,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]
    def _do_retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        return self._search_by_vector(self.encode_query(query), top_k)

    def _search_by_vector(self, vector: list[float], top_k: int) -> list[RetrievalResult]:
        n = self.size
        if n == 0:
            return []
        res = self._collection.query(
            query_embeddings=[vector],
            n_results=min(top_k, n),
            include=["documents", "metadatas", "distances"],
        )
        ids = res.get("ids", [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        out: list[RetrievalResult] = []
        for i, cid in enumerate(ids):
            distance = float(dists[i]) if i < len(dists) else 0.0
            out.append(
                RetrievalResult(
                    chunk_id=str(cid),
                    text=str(docs[i]) if i < len(docs) else "",
                    # 余弦距离转相似度，保证分数越大越相关的约定
                    score=1.0 - distance,
                    metadata=dict(metas[i]) if i < len(metas) else {},
                )
            )
        return out

    def retrieve_batch(self, queries: Sequence[str], top_k: int | None = None,
                       show_progress: bool = False) -> list[list[RetrievalResult]]:
        """批量检索：查询批量编码（真正用上 GPU/多核批推理），再逐条查库。"""
        if not self._loaded:
            self.warmup()
        k = top_k or self.default_top_k
        if not queries:
            return []

        if show_progress:
            from tqdm import tqdm

            iterator = tqdm(range(0, len(queries), 32), desc="向量检索", unit="批", ncols=90)
        else:
            iterator = range(0, len(queries), 32)

        results: list[list[RetrievalResult]] = []
        for start in iterator:
            batch = list(queries)[start : start + 32]
            for vec in self.encode_queries(batch):
                hits = self._search_by_vector(vec, k)
                for i, item in enumerate(hits, 1):
                    item.rank = i
                    item.retriever = self.name
                    item.matched_by.append(self.name)
                results.append(hits)
        return results

    def info(self) -> dict[str, Any]:
        data = super().info()
        data.update({
            "kb_id": self.kb_id,
            "collection": self.collection_name,
            "model": settings.embedding.resolved_model,
            "device": self._device,
            "query_instruction": settings.embedding.use_query_instruction,
        })
        return data
