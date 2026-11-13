from types import SimpleNamespace

from langchain_core.documents import Document

from src.db.models import StrategyPreset
from src.retrieval.retrievers import build_retriever
from src.retrieval.scope import RetrievalScope

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "retrieve",
            "description": "在指定知识库中检索制度原文，返回带编号的证据片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或完整问题"},
                    "strategy": {
                        "type": "string",
                        "enum": ["hybrid_rerank", "hybrid", "vector", "bm25"],
                        "description": "检索策略，默认混合加精排",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "description": "返回证据条数，默认 4",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarification",
            "description": "问题缺少关键信息时，向用户提出一个澄清问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "需要用户补充的信息"},
                },
                "required": ["question"],
            },
        },
    },
]

STRATEGY_FLAGS = {
    "vector": (True, False, False),
    "bm25": (False, True, False),
    "hybrid": (True, True, False),
    "hybrid_rerank": (True, True, True),
}


def build_strategy_view(
    preset: StrategyPreset | None, strategy: str, top_k: int | None
) -> tuple[SimpleNamespace, int]:
    """把工具参数转换为检索预设视图，权限过滤仍由检索层统一执行。"""
    use_vector, use_bm25, rerank = STRATEGY_FLAGS.get(strategy, STRATEGY_FLAGS["hybrid_rerank"])
    limit = max(1, min(int(top_k or 4), 8))
    view = SimpleNamespace(
        use_vector=use_vector,
        use_bm25=use_bm25,
        vector_top_k=max(limit * 2, preset.vector_top_k if preset else limit * 2),
        bm25_top_k=max(limit * 2, preset.bm25_top_k if preset else limit * 2),
        vector_weight=preset.vector_weight if preset else 0.5,
        bm25_weight=preset.bm25_weight if preset else 0.5,
        rrf_k=preset.rrf_k if preset else 60,
        rerank=rerank,
        rerank_top_n=limit,
    )
    return view, limit


def chunk_identity(document: Document) -> str:
    metadata = document.metadata
    if metadata.get("chunk_id"):
        return str(metadata.get("chunk_id"))
    return f"{metadata.get('document_id')}:{metadata.get('article_no')}:{hash(document.page_content[:200])}"


async def retrieve_documents(
    kb_id: int,
    preset: StrategyPreset | None,
    scope: RetrievalScope,
    query: str,
    strategy: str,
    top_k: int | None,
) -> list[Document]:
    view, limit = build_strategy_view(preset, strategy, top_k)
    bundle = build_retriever(kb_id, view, scope)
    documents = await bundle.retriever.ainvoke(query)
    return documents[:limit]
