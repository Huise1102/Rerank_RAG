"""docx 解析器，用 python-docx 逐段落抽取文本，段落序号作为页码。

空段落跳过，页码按保留的段落从 1 递增。
"""

from __future__ import annotations

from pathlib import Path

from src.ingest.loader import LoadedPage


def parse_docx(path: str | Path) -> list[LoadedPage]:
    """逐段落抽取 docx 文本，返回加载页列表，每段一页。"""
    from docx import Document

    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"DOCX 不存在: {p}")

    document = Document(str(p))
    paragraphs = [para.text.strip() for para in document.paragraphs]
    kept = [text for text in paragraphs if text]
    total = len(kept)

    pages: list[LoadedPage] = []
    for i, text in enumerate(kept, start=1):
        pages.append(
            LoadedPage(
                source=p.name,
                file_path=str(p),
                page=i,  # 段落序号，从 1 开始
                total_pages=total,
                text=text,
            )
        )
    return pages
