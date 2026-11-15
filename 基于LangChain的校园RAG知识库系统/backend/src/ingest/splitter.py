import hashlib
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.core.config import settings

CHINESE_SEPARATORS = ["\n第", "\n\n", "\n", "。", "；", "，", " ", ""]
CHAPTER_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千0-9]+章)\s*(.*)$")
SECTION_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千0-9]+节)\s*(.*)$")
ARTICLE_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千0-9]+条)\s*(.*)$")
SENTENCE_RE = re.compile(r"(?<=[。；！？])")
PAGE_MARK_RE = re.compile(r"^\[\[PAGE:(\d+)\]\]$")


def _hash(text: str, length: int = 40) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def _recursive_chunks(documents: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=CHINESE_SEPARATORS,
        keep_separator=True,
        length_function=len,
    )
    return splitter.split_documents(documents)


def _strip_page_marks(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not PAGE_MARK_RE.match(line.strip())
    )


def _split_table(document: Document, meta: dict) -> list[Document]:
    lines = [line for line in document.page_content.splitlines() if line.strip()]
    max_rows = max(settings.table_max_rows, 2)
    prefix_parts = [meta.get("section_title"), meta.get("table_caption")]
    prefix = " · ".join(str(part) for part in prefix_parts if part)

    def wrap(content: str) -> str:
        return f"{prefix}\n{content}" if prefix else content

    if len(lines) <= max_rows + 2:
        return [
            Document(
                page_content=wrap(document.page_content),
                metadata=dict(meta, chunk_type="table"),
            )
        ]
    header = lines[:2]
    body = lines[2:]
    chunks: list[Document] = []
    step = max_rows
    for start in range(0, len(body), step):
        part = header + body[start : start + step]
        chunks.append(
            Document(
                page_content=wrap("\n".join(part)),
                metadata=dict(
                    meta,
                    chunk_type="table",
                    row_start=start + 1,
                    row_end=min(start + step, len(body)),
                ),
            )
        )
    return chunks


def _split_long_article(
    text: str, article_no: str, parent_text: str, base_meta: dict
) -> list[str]:
    if len(text) <= settings.clause_max_chars:
        return [text]
    pieces: list[str] = []
    buffer = ""
    for sentence in SENTENCE_RE.split(text):
        if not sentence:
            continue
        if buffer and len(buffer) + len(sentence) > settings.clause_max_chars:
            pieces.append(buffer.strip())
            buffer = sentence
        else:
            buffer += sentence
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces or [text]


def _clause_chunks(document: Document, meta: dict) -> list[Document]:
    lines = [line.strip() for line in document.page_content.splitlines() if line.strip()]
    chapters: list[str] = []
    sections: list[str] = []
    blocks: list[tuple[str, str, int | None, int | None]] = []
    current_article: str | None = None
    try:
        current_page: int | None = int(meta.get("page"))
    except (TypeError, ValueError):
        current_page = None
    buffer: list[str] = []
    buffer_start_page: int | None = None
    buffer_end_page: int | None = None

    def flush_article() -> None:
        nonlocal current_article, buffer, buffer_start_page, buffer_end_page
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                blocks.append((current_article or "", text, buffer_start_page, buffer_end_page))
        buffer = []
        buffer_start_page = None
        buffer_end_page = None

    for line in lines:
        page_mark = PAGE_MARK_RE.match(line)
        if page_mark:
            current_page = int(page_mark.group(1))
            continue
        chapter = CHAPTER_RE.match(line)
        if chapter:
            flush_article()
            chapters = [f"{chapter.group(1)} {chapter.group(2)}".strip()]
            sections = []
            continue
        section = SECTION_RE.match(line)
        if section:
            flush_article()
            sections = [f"{section.group(1)} {section.group(2)}".strip()]
            continue
        article = ARTICLE_RE.match(line)
        if article:
            flush_article()
            current_article = article.group(1)
            buffer = [line]
            buffer_start_page = current_page
            buffer_end_page = current_page
            continue
        if buffer_start_page is None:
            buffer_start_page = current_page
        if current_page is not None:
            buffer_end_page = current_page
        buffer.append(line)
    flush_article()

    if not any(article_no for article_no, _, _, _ in blocks):
        return []

    context_parts = [
        meta.get("section_title"),
        meta.get("section_doc_no") or meta.get("doc_no"),
        *chapters,
        *sections,
    ]
    parent_context = "\n".join(str(part) for part in context_parts if part)
    chunks: list[Document] = []
    for article_no, text, start_page, end_page in blocks:
        full_text = text
        if article_no and not text.startswith(article_no):
            full_text = f"{article_no} {text}"
        article_parent = "\n".join(
            part for part in (parent_context, full_text[: settings.parent_text_max_chars]) if part
        )
        parent_chunk_id = _hash(
            f"{meta.get('section_index')}:{meta.get('version_id')}:{article_no}:{full_text}"
        )
        article_type = "clause" if article_no else "text"
        for piece in _split_long_article(full_text, article_no, article_parent, meta):
            chunks.append(
                Document(
                    page_content=piece,
                    metadata=dict(
                        meta,
                        chunk_type=article_type,
                        article_no=article_no or None,
                        parent_chunk_id=parent_chunk_id,
                        parent_text=article_parent[: settings.parent_text_max_chars],
                        page=start_page or meta.get("page", "unknown"),
                        page_end=end_page or meta.get("page_end", meta.get("page", "unknown")),
                    ),
                )
            )
    return chunks


def _finalize(chunks: list[Document], document_id: int, version_id: int) -> list[Document]:
    result: list[Document] = []
    seen: set[str] = set()
    for index, chunk in enumerate(chunks):
        text = (chunk.page_content or "").strip()
        if not text:
            continue
        content_hash = _hash(text)
        if content_hash in seen:
            continue
        seen.add(content_hash)
        metadata = dict(chunk.metadata)
        metadata.update(
            {
                "chunk_id": f"{document_id}:{version_id}:{index}:{content_hash[:8]}",
                "chunk_index": index,
                "content_hash": content_hash,
                "text_length": len(text),
                "page": metadata.get("page", "unknown"),
                "page_end": metadata.get("page_end", metadata.get("page", "unknown")),
            }
        )
        result.append(Document(page_content=text, metadata=metadata))
    return result


def split_documents(
    documents: list[Document],
    strategy: str | None = None,
    doc_meta: dict | None = None,
) -> list[Document]:
    strategy = strategy or settings.chunk_strategy
    doc_meta = doc_meta or {}
    document_id = int(doc_meta.get("document_id") or 0)
    version_id = int(doc_meta.get("version_id") or 0)

    prepared: list[Document] = []
    for document in documents:
        base_meta = dict(document.metadata)
        base_meta.update(doc_meta)
        if base_meta.get("chunk_type") == "table":
            prepared.extend(_split_table(document, base_meta))
        else:
            prepared.append(Document(page_content=document.page_content, metadata=base_meta))

    chunks: list[Document] = []
    if strategy == "clause":
        for document in prepared:
            if document.metadata.get("chunk_type") == "table":
                chunks.append(document)
                continue
            clause_chunks = _clause_chunks(document, document.metadata)
            if clause_chunks:
                chunks.extend(clause_chunks)
            else:
                clean = Document(
                    page_content=_strip_page_marks(document.page_content),
                    metadata=document.metadata,
                )
                fallback = _recursive_chunks([clean])
                for item in fallback:
                    item.metadata.update(document.metadata, chunk_type="text")
                chunks.extend(fallback)
    elif strategy == "fixed":
        for document in prepared:
            if document.metadata.get("chunk_type") != "table":
                document.page_content = _strip_page_marks(document.page_content)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=[""],
            length_function=len,
        )
        chunks = splitter.split_documents(prepared)
    else:
        text_documents = [
            Document(
                page_content=_strip_page_marks(item.page_content),
                metadata=item.metadata,
            )
            for item in prepared
            if item.metadata.get("chunk_type") != "table"
        ]
        tables = [item for item in prepared if item.metadata.get("chunk_type") == "table"]
        chunks = _recursive_chunks(text_documents) + tables

    return _finalize(chunks, document_id, version_id)
