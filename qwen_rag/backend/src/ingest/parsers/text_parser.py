"""txt / md 解析器：直接读取，整体作为一「文档」的一页。

md 保留原文标记（``#``、``-``、``**`` 等）不剥离——这些都携带结构信息，剥离反而损失语义；
txt 与 md 走同一条路径，page = 1，total_pages = 1。
"""

from __future__ import annotations

from pathlib import Path

from src.ingest.loader import LoadedPage

#: 按序尝试的编码（中文文本常见 GBK/GB18030，最后兜底 replace）
_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def _read_text(path: Path) -> str:
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    # 所有编码都失败时兜底，避免整个入库流程因个别坏文件中断
    return path.read_text(encoding="utf-8", errors="replace")


def parse_text(path: str | Path) -> list[LoadedPage]:
    """读取 txt / md，返回单页 ``LoadedPage``。"""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")

    text = _read_text(p).strip()
    return [
        LoadedPage(
            source=p.name,
            file_path=str(p),
            page=1,
            total_pages=1,
            text=text,
        )
    ]
