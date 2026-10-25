"""文档加载器（loader.py）：**按扩展名分派**到 ``src/ingest/parsers/``，统一产出 ``LoadedPage``。

四种格式（pdf / docx / txt / md）收敛到同一结构，后续切分 / 索引 / 引用溯源对格式无感知：

- pdf : pdfplumber 逐页抽取（页眉页脚/断行清洗）→ page = 页码（从 1）
- docx: python-docx 逐段落抽文本 → page = 段落序号（从 1），total_pages = 段落数
- txt : 直接读取 → 整体作为一页，page = 1，total_pages = 1
- md  : 与 txt 相同，保留原文标记不剥离

主流程::

    pages = load_document("product/resource/法律领域/xxx.pdf")   # -> list[LoadedPage]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class LoadedPage:
    """统一的一「页/段」结构：所有解析器共用。"""

    source: str  # 文件名，如 "transformer.pdf"
    file_path: str  # 绝对路径
    page: int | str  # pdf=页码（从 1）/ docx=段落序号（从 1）/ txt,md=1
    total_pages: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


def supported_extensions() -> tuple[str, ...]:
    """支持的文件类型（小写、不含点）。"""
    return ("pdf", "docx", "txt", "md")


def load_document(path: str | Path) -> list[LoadedPage]:
    """按扩展名分派加载单个文档，返回统一的 ``LoadedPage`` 列表。

    不支持的类型抛 ``ValueError``。解析器在函数内惰性导入，避免导入 loader 时即拉起
    pdfplumber / python-docx 等重依赖。
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
