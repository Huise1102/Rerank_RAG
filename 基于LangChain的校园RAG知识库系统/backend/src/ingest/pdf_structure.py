"""PDF 结构化解析：清洗页眉页脚、剔除表格区域、合并跨页表格、识别文件分节。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.documents import Document

PAGE_MARK = "[[PAGE:{page}]]"
PAGE_MARK_RE = re.compile(r"^\[\[PAGE:(\d+)\]\]$")
PAGE_NO_RE = re.compile(r"^[\s\-—–_]*\d{1,4}[\s\-—–_]*$")
DASH_RE = re.compile(r"^[\s\-—–_]{2,}$")
TOC_LINE_RE = re.compile(r"^(?P<title>.+?)[.…·]{3,}\s*(?P<page>\d{1,4})\s*$")
TOC_PAGE_RE = re.compile(r"^(?P<title>.{6,}?)\s*(?P<page>\d{1,4})\s*$")
DOC_NO_RE = re.compile(r"宿院〔\s*(\d{4})\s*〕\s*(\d+)\s*号")
CHAPTER_RE = re.compile(r"^\s*第[一二三四五六七八九十百千0-9]+章")
ARTICLE_RE = re.compile(r"^\s*第[一二三四五六七八九十百千0-9]+条")
TITLE_SUFFIXES = ("办法", "意见", "方案", "规定", "细则", "规则", "标准", "通知")


@dataclass
class TableData:
    rows: list[list[str]]
    page: int
    page_end: int
    bbox: tuple[float, float, float, float]
    page_height: float
    caption: str = ""


@dataclass
class PageInfo:
    number: int
    text: str
    raw_text: str
    height: float
    tables: list[TableData] = field(default_factory=list)
    is_toc: bool = False


@dataclass
class Section:
    start_page: int
    end_page: int
    title: str


def normalize_cell(value: object) -> str:
    text = str(value or "").replace("|", "／").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def table_to_markdown(rows: list[list[str | None]]) -> str:
    """把表格行转成 Markdown，补齐列宽并去掉全空列。"""
    cleaned: list[list[str]] = []
    for row in rows:
        cells = [normalize_cell(cell) for cell in row]
        if any(cells):
            cleaned.append(cells)
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    normalized = [row + [""] * (width - len(row)) for row in cleaned]
    keep = [index for index in range(width) if any(row[index] for row in normalized)]
    if not keep:
        return ""
    normalized = [[row[index] for index in keep] for row in normalized]
    header = list(normalized[0])
    for index in range(1, len(header)):
        if not header[index]:
            header[index] = header[index - 1]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _clean_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or PAGE_NO_RE.match(line) or DASH_RE.match(line):
            continue
        if TOC_LINE_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _text_without_tables(page, bboxes: list[tuple]) -> str:
    if not bboxes:
        return (page.extract_text() or "").strip()

    def keep(obj) -> bool:
        if obj.get("object_type") != "char":
            return True
        center_x = (obj["x0"] + obj["x1"]) / 2
        center_y = (obj["top"] + obj["bottom"]) / 2
        return not any(
            bbox[0] - 1 <= center_x <= bbox[2] + 1 and bbox[1] - 1 <= center_y <= bbox[3] + 1
            for bbox in bboxes
        )

    return (page.filter(keep).extract_text() or "").strip()


def _caption_for(page, bbox: tuple[float, float, float, float]) -> str:
    try:
        lines = page.extract_text_lines()
    except Exception:
        return ""
    for line in reversed(lines):
        text = (line.get("text") or "").strip()
        if line.get("bottom", 0) > bbox[1] - 1:
            continue
        if not (4 <= len(text) <= 80) or PAGE_NO_RE.match(text) or DASH_RE.match(text):
            continue
        if text.endswith("表") or any(word in text for word in ("标准", "系数", "一览", "明细")) or text.startswith("第"):
            return text
    return ""


def _table_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in table.extract():
        cells = [normalize_cell(cell) for cell in raw]
        if any(cells):
            rows.append(cells)
    return rows


def read_pages(path: Path) -> tuple[list[PageInfo], list[str]]:
    import pdfplumber

    pages: list[PageInfo] = []
    warnings: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            try:
                found = page.find_tables()
            except Exception as exc:
                warnings.append(f"第 {number} 页表格检测失败：{exc}")
                found = []
            tables: list[TableData] = []
            for table in found:
                try:
                    rows = _table_rows(table)
                except Exception as exc:
                    warnings.append(f"第 {number} 页表格解析失败：{exc}")
                    continue
                if not rows:
                    continue
                tables.append(
                    TableData(
                        rows=rows,
                        page=number,
                        page_end=number,
                        bbox=tuple(table.bbox),
                        page_height=float(page.height),
                        caption=_caption_for(page, tuple(table.bbox)),
                    )
                )
            raw_text = _text_without_tables(page, [item.bbox for item in found])
            pages.append(
                PageInfo(
                    number=number,
                    text=_clean_text(raw_text),
                    raw_text=raw_text,
                    height=float(page.height),
                    tables=tables,
                )
            )
    return pages, warnings


def _normalize_title(title: str) -> str:
    title = title.strip()
    if "》" in title and "《" not in title:
        title = f"《{title}"
    return title


def _is_toc_page(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    dot_lines = sum(1 for line in lines if TOC_LINE_RE.match(line))
    if dot_lines >= 3:
        return True
    head = "\n".join(lines[:6])
    return ("目 录" in head or "目录" in head) and dot_lines >= 2


def _toc_entries(pages: list[PageInfo]) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    toc_pages: list[PageInfo] = []
    toc_end = 0
    for page in pages:
        if not _is_toc_page(page.raw_text):
            continue
        toc_pages.append(page)
        toc_end = max(toc_end, page.number)
        pending = ""
        for raw_line in page.raw_text.splitlines():
            line = raw_line.strip()
            if not line or PAGE_NO_RE.match(line) or DASH_RE.match(line):
                continue
            if line in {"目录", "目 录"}:
                continue
            match = TOC_LINE_RE.match(line)
            if not match:
                candidate = TOC_PAGE_RE.match(line)
                if candidate and int(candidate.group("page")) <= len(pages):
                    match = candidate
            if match:
                title_part = match.group("title").strip()
                if pending:
                    title = f"{pending} {title_part}".strip()
                    pending = ""
                else:
                    title = re.sub(
                        r"^\d+\s*[.、]?\s*[《]?[.、]?\s*", "", title_part
                    ).strip()
                title = re.sub(r"[.…·]{3,}$", "", title).strip()
                entries.append((int(match.group("page")), _normalize_title(title)))
            elif len(line) <= 100:
                if not pending:
                    line = re.sub(r"^\d+\s*[.、]?\s*[《]?[.、]?\s*", "", line).strip()
                pending = f"{pending} {line}".strip() if pending else line
                if len(pending) > 120:
                    pending = ""
    if len(entries) < 2:
        return []
    offset = toc_end
    mapped = [(printed + offset, title) for printed, title in entries]
    first_content = toc_end + 1
    if any(page < first_content or page > len(pages) for page, _ in mapped):
        return []
    result: list[tuple[int, str]] = []
    for page, title in mapped:
        if result and page <= result[-1][0]:
            continue
        result.append((page, title))
    if not result:
        return []
    if result[0][0] > first_content:
        result[0] = (first_content, result[0][1])
    for page in toc_pages:
        page.is_toc = True
        page.text = ""
    return result


def _looks_like_title(text: str) -> bool:
    text = text.strip()
    if not (6 <= len(text) <= 100):
        return False
    if text.startswith("附件"):
        return True
    normalized = re.sub(r"[（(][^）)]{1,12}[）)]$", "", text).strip()
    return "宿迁学院" in normalized and any(
        normalized.endswith(suffix) for suffix in TITLE_SUFFIXES
    )


def _has_structure(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return any(CHAPTER_RE.match(line) or ARTICLE_RE.match(line) for line in lines[:25])


def _section_candidates(pages: list[PageInfo]) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for index, page in enumerate(pages):
        if page.is_toc:
            continue
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        title = ""
        for position, line in enumerate(lines[:12]):
            if re.fullmatch(r"附件\s*\d+", line) and position + 1 < len(lines):
                title = f"{line} {lines[position + 1]}"
                break
            if line.startswith("关于印发") and position + 1 < len(lines):
                title = f"{line}{lines[position + 1]}"
                break
            if _looks_like_title(line):
                title = line
                break
        if not title:
            continue
        next_has = bool(index + 1 < len(pages) and _has_structure(pages[index + 1].text))
        if not (_has_structure(page.text) or next_has):
            continue
        if candidates and candidates[-1][1] == title:
            continue
        candidates.append((page.number, _normalize_title(title)))
    return candidates


def _detect_sections(
    pages: list[PageInfo], default_title: str, toc: list[tuple[int, str]]
) -> list[Section]:
    if toc:
        starts: list[tuple[int, str]] = []
        for page, title in toc:
            if starts and page <= starts[-1][0]:
                continue
            starts.append((page, title))
        if len(starts) >= 2:
            sections: list[Section] = []
            for index, (start, title) in enumerate(starts):
                end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(pages)
                sections.append(Section(start_page=start, end_page=end, title=title))
            return sections

    candidates = _section_candidates(pages)
    if not candidates:
        return [Section(start_page=1, end_page=len(pages), title=default_title)]
    sections = []
    for index, (start, title) in enumerate(candidates):
        if index == 0 and start > 1:
            start = 1
        end = candidates[index + 1][0] - 1 if index + 1 < len(candidates) else len(pages)
        sections.append(Section(start_page=start, end_page=end, title=title))
    return sections


def _strip_repeated_lines(pages: list[PageInfo]) -> None:
    counts: Counter[str] = Counter()
    for page in pages:
        if page.is_toc:
            continue
        for line in set(line.strip() for line in page.text.splitlines() if line.strip()):
            if len(line) <= 40:
                counts[line] += 1
    threshold = max(3, int(len(pages) * 0.6))
    repeated = {
        line
        for line, count in counts.items()
        if count >= threshold and not any(mark in line for mark in ("。", "；", "！", "？"))
    }
    if not repeated:
        return
    for page in pages:
        page.text = "\n".join(
            line for line in page.text.splitlines() if line.strip() not in repeated
        ).strip()


def _merge_tables(pages: list[PageInfo]) -> tuple[list[TableData], int]:
    merged: list[TableData] = []
    merged_count = 0
    for page in pages:
        for table in page.tables:
            if table.page == page.number and merged:
                previous = merged[-1]
                same_width = len(previous.rows[0]) == len(table.rows[0])
                if (
                    table.page == previous.page_end + 1
                    and previous.bbox[3] >= previous.page_height * 0.75
                    and table.bbox[1] <= table.page_height * 0.25
                    and same_width
                ):
                    rows = table.rows
                    if rows and rows[0] == previous.rows[0]:
                        rows = rows[1:]
                    previous.rows.extend(rows)
                    previous.page_end = table.page_end
                    merged_count += 1
                    continue
            merged.append(table)
    return merged, merged_count


def find_doc_no(text: str) -> str:
    match = DOC_NO_RE.search(re.sub(r"\s+", "", text))
    return match.group(0) if match else ""


def build_pdf_documents(
    path: Path, metadata: dict
) -> tuple[list[Document], list[str], dict]:
    """解析 PDF 并按识别出的文件分节生成文本与表格文档。"""
    pages, warnings = read_pages(path)
    default_title = metadata.get("doc_title") or metadata.get("filename") or path.stem
    toc = _toc_entries(pages)
    _strip_repeated_lines(pages)
    sections = _detect_sections(pages, str(default_title), toc)
    tables, merged_count = _merge_tables(pages)

    documents: list[Document] = []
    for index, section in enumerate(sections):
        section_pages = [page for page in pages if section.start_page <= page.number <= section.end_page]
        parts = [
            f"{PAGE_MARK.format(page=page.number)}\n{page.text}"
            for page in section_pages
            if page.text.strip()
        ]
        section_text = "\n".join(parts).strip()
        doc_no = find_doc_no(section_text)
        base = dict(
            metadata,
            section_index=index,
            section_title=section.title,
            section_doc_no=doc_no,
            page=section.start_page,
            page_end=section.end_page,
        )
        if section_text:
            documents.append(
                Document(page_content=section_text, metadata=dict(base, chunk_type="text"))
            )
        for table_index, table in enumerate(
            [item for item in tables if section.start_page <= item.page <= section.end_page], start=1
        ):
            markdown = table_to_markdown(table.rows)
            if not markdown:
                continue
            documents.append(
                Document(
                    page_content=markdown,
                    metadata=dict(
                        base,
                        chunk_type="table",
                        page=table.page,
                        page_end=table.page_end,
                        table_id=f"s{index}-t{table_index}",
                        table_caption=table.caption,
                    ),
                )
            )

    empty_pages = sum(
        1 for page in pages if not page.text.strip() and not page.tables
    )
    stats = {
        "pages": len(pages),
        "sections": len(sections),
        "source_tables": sum(len(page.tables) for page in pages),
        "merged_tables": merged_count,
        "empty_pages": empty_pages,
        "ocr_suspected": bool(pages) and empty_pages / len(pages) >= 0.5,
    }
    if stats["ocr_suspected"]:
        warnings.append("超过一半页面没有文本层，疑似扫描版 PDF，请提供可复制文本的版本或启用 OCR")
    return documents, warnings, stats
