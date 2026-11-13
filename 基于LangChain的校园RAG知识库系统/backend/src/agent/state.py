from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document

from src.retrieval.scope import RetrievalScope


@dataclass
class AgentState:
    """深度问答一次请求的可变状态。"""

    question: str
    kb_id: int
    scope: RetrievalScope
    messages: list[Any] = field(default_factory=list)
    evidence: list[Document] = field(default_factory=list)
    seen_chunks: set[str] = field(default_factory=set)
    trace: list[dict] = field(default_factory=list)
    rounds: int = 0
    retrievals: int = 0
    clarifications: int = 0
    final_answer: str = ""
    failed: str | None = None
