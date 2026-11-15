import logging
from pathlib import Path

from langchain_core.documents import Document

from src.ingest.pdf_structure import build_pdf_documents, table_to_markdown

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def _docx_documents(path: Path, metadata: dict, warnings: list[str]) -> list[Document]:
    from docx import Document as DocxDocument
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = DocxDocument(str(path))
    documents: list[Document] = []
    buffer: list[str] = []
    table_index = 0

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if text:
            documents.append(
                Document(page_content=text, metadata=dict(metadata, chunk_type="text"))
            )

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = (item.text or "").strip()
            if text:
                buffer.append(text)
        elif isinstance(item, Table):
            flush()
            table_index += 1
            rows = [[cell.text for cell in row.cells] for row in item.rows]
            markdown = table_to_markdown(rows)
            if markdown:
                documents.append(
                    Document(
                        page_content=markdown,
                        metadata=dict(
                            metadata,
                            chunk_type="table",
                            table_id=f"t{table_index}",
                        ),
                    )
                )
            else:
                warnings.append(f"第 {table_index} 个表格内容为空，已跳过")
    flush()
    return documents


def load_document_with_report(
    path: Path, kb_id: int, document_id: int, filename: str
) -> tuple[list[Document], list[str], dict]:
    suffix = path.suffix.lower()
    metadata: dict = {
        "kb_id": kb_id,
        "document_id": document_id,
        "source": filename,
        "filename": filename,
    }
    warnings: list[str] = []
    stats: dict = {
        "pages": 1,
        "sections": 1,
        "source_tables": 0,
        "merged_tables": 0,
        "empty_pages": 0,
        "ocr_suspected": False,
    }
    if suffix == ".pdf":
        documents, warnings, stats = build_pdf_documents(path, metadata)
    elif suffix == ".docx":
        documents = _docx_documents(path, metadata, warnings)
        stats["source_tables"] = sum(
            1 for item in documents if item.metadata.get("chunk_type") == "table"
        )
    elif suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        documents = (
            [Document(page_content=text, metadata=dict(metadata, chunk_type="text"))]
            if text
            else []
        )
    else:
        raise ValueError(f"不支持的文件类型：{suffix}")

    cleaned: list[Document] = []
    for document in documents:
        text = (document.page_content or "").strip()
        if not text:
            continue
        document.page_content = text
        cleaned.append(document)
    if not cleaned:
        warnings.append("文档未解析出有效文本")
    for warning in warnings:
        logger.warning("文档解析提示 %s：%s", filename, warning)
    return cleaned, warnings, stats


def load_document(path: Path, kb_id: int, document_id: int, filename: str) -> list[Document]:
    documents, _, _ = load_document_with_report(path, kb_id, document_id, filename)
    return documents
