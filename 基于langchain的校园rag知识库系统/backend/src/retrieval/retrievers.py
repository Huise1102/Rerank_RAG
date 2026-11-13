from dataclasses import dataclass, field
from functools import lru_cache

from langchain.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

from src.core.config import settings
from src.db.models import StrategyPreset
from src.retrieval import bm25
from src.retrieval.vector_store import get_vector_store


class TrackingRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseRetriever
    bucket: list[Document] = Field(default_factory=list)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        documents = self.inner.invoke(query)
        self.bucket.clear()
        self.bucket.extend(documents)
        return documents


@dataclass
class RetrievalBundle:
    retriever: BaseRetriever
    vector_tracker: TrackingRetriever | None = None
    bm25_tracker: TrackingRetriever | None = None
    used_rerank: bool = False
    fields: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def get_cross_encoder() -> HuggingFaceCrossEncoder:
    return HuggingFaceCrossEncoder(
        model_name=settings.reranker_model,
        model_kwargs={"device": settings.embedding_device},
    )


def build_retriever(kb_id: int, preset: StrategyPreset | None) -> RetrievalBundle:
    use_vector = preset.use_vector if preset else True
    use_bm25 = preset.use_bm25 if preset else True
    vector_top_k = preset.vector_top_k if preset else settings.vector_top_k
    bm25_top_k = preset.bm25_top_k if preset else settings.bm25_top_k
    vector_weight = preset.vector_weight if preset else 0.5
    bm25_weight = preset.bm25_weight if preset else 0.5
    rrf_k = preset.rrf_k if preset else settings.rrf_k
    rerank = preset.rerank if preset else settings.rerank_enabled
    rerank_top_n = preset.rerank_top_n if preset else settings.rerank_top_n

    if not use_vector and not use_bm25:
        raise ValueError("检索策略必须至少启用一种召回方式")

    retrievers: list[BaseRetriever] = []
    weights: list[float] = []
    vector_tracker: TrackingRetriever | None = None
    bm25_tracker: TrackingRetriever | None = None

    if use_vector:
        vector_base = get_vector_store(kb_id).as_retriever(
            search_kwargs={"k": vector_top_k, "filter": {"kb_id": kb_id}}
        )
        vector_tracker = TrackingRetriever(inner=vector_base)
        retrievers.append(vector_tracker)
        weights.append(max(vector_weight, 0.01))

    if use_bm25:
        bm25_base = bm25.get_retriever(kb_id, bm25_top_k)
        if bm25_base is not None:
            bm25_tracker = TrackingRetriever(inner=bm25_base)
            retrievers.append(bm25_tracker)
            weights.append(max(bm25_weight, 0.01))

    if not retrievers:
        raise ValueError("知识库索引为空，请先上传文档并重建索引")

    if len(retrievers) == 1:
        base: BaseRetriever = retrievers[0]
    else:
        base = EnsembleRetriever(retrievers=retrievers, weights=weights, c=rrf_k)

    used_rerank = False
    if rerank:
        compressor = CrossEncoderReranker(model=get_cross_encoder(), top_n=rerank_top_n)
        base = ContextualCompressionRetriever(base_compressor=compressor, base_retriever=base)
        used_rerank = True

    return RetrievalBundle(
        retriever=base,
        vector_tracker=vector_tracker,
        bm25_tracker=bm25_tracker,
        used_rerank=used_rerank,
        fields={
            "use_vector": use_vector,
            "use_bm25": use_bm25,
            "vector_top_k": vector_top_k,
            "bm25_top_k": bm25_top_k,
            "rrf_k": rrf_k,
            "rerank_top_n": rerank_top_n,
        },
    )
