import pytest
from langchain_core.documents import Document
from pydantic import ValidationError

from src.generation.chain import build_citations, format_context
from src.schemas.chat import ChatRequest


def test_chat_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ChatRequest(question="问题", kb_id=1, temperature=0.9)


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
    assert context.startswith("[1]《示例.md》")
    assert "正文" in context


def test_citation_includes_version_and_article():
    documents = [
        Document(
            page_content="第三十八条 转专业条件",
            metadata={
                "filename": "学籍管理规定.pdf",
                "doc_title": "学籍管理规定",
                "page": 12,
                "document_id": 7,
                "article_no": "第三十八条",
                "version_label": "2025",
                "effective_date": "2025-09-01",
                "department_name": "教务处",
            },
        )
    ]
    citation = build_citations(documents)[0]
    assert citation["article_no"] == "第三十八条"
    assert citation["version_label"] == "2025"
    assert citation["department"] == "教务处"
    assert "第 12 页" in citation["label"]
