"""文档加载器，按扩展名分派到各格式解析器，统一产出页面结构。

四种格式收敛到同一结构，切分、索引与引用溯源对格式无感知。
PDF 用 pdfplumber 逐页抽取，并清洗页眉页脚与断行，页码从 1 开始。
DOCX 用 python-docx 逐段落抽取文本，段落序号作为页码，总页数为段落数。
TXT 与 MD 整体作为一页，页码与总页数均为 1，MD 保留原文标记。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class LoadedPage:
    """统一的页面结构，所有解析器共用。"""

    source: str  # 文件名
    file_path: str  # 绝对路径
    page: int | str  # PDF 为页码从 1 开始，DOCX 为段落序号从 1 开始，TXT 与 MD 为 1
    total_pages: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


def supported_extensions() -> tuple[str, ...]:
    """支持的文件类型，小写且不含点。"""
    return ("pdf", "docx", "txt", "md")


def load_document(path: str | Path) -> list[LoadedPage]:
    """按扩展名分派加载单个文档，返回统一的页面列表。

    不支持的类型抛 ValueError。解析器在函数内惰性导入，避免导入加载器时即拉起重依赖。
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
