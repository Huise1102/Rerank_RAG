import pytest
from langchain_core.documents import Document
from pydantic import ValidationError

from src.generation.chain import build_citations, format_context
from src.schemas.chat import ChatRequest


def test_chat_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ChatRequest(question="问题", kb_id=1, model="gpt-4o")


def test_build_citations_uses_order_and_metadata():
    documents = [
        Document(
            page_content="第一条 适用范围",
            metadata={"filename": "示例.md", "page": 2, "document_id": 7},
        )
    ]
    citations = build_citations(documents)
    assert citations[0]["index"] == 1
    assert citations[0]["filename"] == "示例.md"
    assert citations[0]["page"] == 2


def test_format_context_marks_index():
    documents = [Document(page_content="正文", metadata={"filename": "示例.md"})]
    context = format_context(documents)
    assert context.startswith("[1] 示例.md")
    assert "正文" in context
