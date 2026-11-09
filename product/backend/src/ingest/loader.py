"""文档加载器，按扩展名分派到对应解析器，统一产出加载页结构。

pdf 逐页抽取并清洗页眉页脚，docx 逐段落抽取，txt 与 md 整体作为一页。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class LoadedPage:
    """统一的一「页/段」结构：所有解析器共用。"""

    source: str  # 文件名，如 "transformer.pdf"
    file_path: str  # 绝对路径
    page: int | str  # pdf 为页码，docx 为段落序号，均从 1 开始；txt 与 md 固定为 1
    total_pages: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


def supported_extensions() -> tuple[str, ...]:
    """支持的文件类型（小写、不含点）。"""
    return ("pdf", "docx", "txt", "md")


def load_document(path: str | Path) -> list[LoadedPage]:
    """按扩展名分派加载单个文档，返回加载页列表。

    不支持的类型抛出 ValueError；解析器在函数内惰性导入，避免加载重依赖。
    """
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")

    if ext == "pdf":
        from src.ingest.parsers.pdf_parser import parse_pdf

        return parse_pdf(p)
    if ext == "docx":
        from src.ingest.parsers.docx_parser import parse_docx

        return parse_docx(p)
    if ext in ("txt", "md"):
        from src.ingest.parsers.text_parser import parse_text

        return parse_text(p)

    raise ValueError(
        f"不支持的文件类型 '.{ext}'，仅支持：{' | '.join(supported_extensions())}"
    )
