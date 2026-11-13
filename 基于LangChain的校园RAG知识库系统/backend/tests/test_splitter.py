from langchain_core.documents import Document

from src.ingest.splitter import split_documents


def test_clause_split_keeps_article_and_parent_context():
    text = (
        "第一章 总则\n"
        "第一条 为规范学生管理，制定本规定。\n"
        "第二条 本规定适用于全体在校学生。\n"
        "第二章 学籍管理\n"
        "第三条 学生应当按时办理注册手续。"
    )
    documents = [Document(page_content=text, metadata={"page": 1, "chunk_type": "text"})]
    chunks = split_documents(
        documents,
        strategy="clause",
        doc_meta={"document_id": 1, "version_id": 2, "version_label": "v1"},
    )
    articles = [item.metadata.get("article_no") for item in chunks]
    assert "第一条" in articles
    assert "第三条" in articles
    third = next(item for item in chunks if item.metadata.get("article_no") == "第三条")
    assert third.metadata.get("chunk_type") == "clause"
    assert "第二章" in third.metadata.get("parent_text", "")
    assert third.metadata.get("parent_chunk_id")


def test_table_document_is_not_split_into_clauses():
    table = "| 项目 | 学分 |\n| --- | --- |\n| 必修 | 100 |"
    documents = [Document(page_content=table, metadata={"page": 3, "chunk_type": "table"})]
    chunks = split_documents(documents, strategy="clause", doc_meta={"document_id": 1, "version_id": 2})
    assert len(chunks) == 1
    assert chunks[0].metadata.get("chunk_type") == "table"


def test_recursive_fallback_when_no_clause():
    documents = [Document(page_content="这是一段没有条款编号的普通说明文字。", metadata={"page": 1})]
    chunks = split_documents(documents, strategy="clause", doc_meta={"document_id": 1, "version_id": 2})
    assert chunks
    assert all(item.metadata.get("chunk_type") == "text" for item in chunks)
    assert all(not item.metadata.get("article_no") for item in chunks)


def test_chunk_ids_are_stable_and_unique():
    documents = [Document(page_content="第一条 学生应当遵守校规。", metadata={"page": 1})]
    first = split_documents(documents, strategy="clause", doc_meta={"document_id": 1, "version_id": 2})
    second = split_documents(documents, strategy="clause", doc_meta={"document_id": 1, "version_id": 2})
    assert [item.metadata["chunk_id"] for item in first] == [
        item.metadata["chunk_id"] for item in second
    ]
    assert len({item.metadata["chunk_id"] for item in first}) == len(first)
