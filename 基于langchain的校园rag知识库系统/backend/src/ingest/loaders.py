from pathlib import Path

from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader
from langchain_core.documents import Document

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def load_document(path: Path, kb_id: int, document_id: int, filename: str) -> list[Document]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        loader = PyPDFLoader(str(path))
    elif suffix == ".docx":
        loader = Docx2txtLoader(str(path))
    elif suffix in {".txt", ".md"}:
        loader = TextLoader(str(path), encoding="utf-8")
    else:
        raise ValueError(f"不支持的文件类型：{suffix}")

    documents = loader.load()
    cleaned: list[Document] = []
    for document in documents:
        metadata: dict = {
            "kb_id": kb_id,
            "document_id": document_id,
            "filename": filename,
        }
        page = document.metadata.get("page")
        if isinstance(page, int):
            metadata["page"] = page + 1
        text = (document.page_content or "").strip()
        if text:
            cleaned.append(Document(page_content=text, metadata=metadata))
    return cleaned
