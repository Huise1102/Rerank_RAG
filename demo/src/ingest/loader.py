"""PDF 加载与文本清洗，把 PDF 转为清洗后的逐页文本，分块由单独的模块负责。

页眉页脚按词级坐标判定，不按文本顺序：提取器的行序不可靠，页眉可能被排到正文之后，按首行或末行判断会漏删页眉或误删正文。正文用带坐标的行按上边界排序重建阅读顺序，避免乱序输出把句子拆散而影响分块与检索。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from config import PDFSettings, settings


class _Default:
    """标记使用全局配置的哨兵对象。"""

    def __repr__(self) -> str:  # pragma: no cover
        return "<default>"


_DEFAULT = _Default()


# 独立成行的页码，兼容短横线包裹、第几页与 Page 等写法
_PAGE_NUMBER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*[-–—~=·.\s]*\d{1,4}\s*[-–—~=·.\s]*$"),
    re.compile(r"^\s*第\s*[0-9０-９]{1,4}\s*页\s*(?:[/／]\s*(?:共)?\s*[0-9０-９]{1,4}\s*页?)?\s*$"),
    re.compile(r"^\s*[Pp]age\s*[:：]?\s*\d{1,4}\s*(?:[/／]?\s*[Oo]f\s*\d{1,4})?\s*$"),
    re.compile(r"^\s*\d{1,4}\s*[/／]\s*\d{1,4}\s*$"),
)

# 中文与中文标点字符
_CJK = r"\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f"

# PDF 提取时插在中文之间的单个空格，需要删除
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}])\s(?=[{_CJK}])")

# 同一字符重复四次以上的行，多为表格边框线被误识别为文字
_REPEAT_NOISE_RE = re.compile(r"^\s*(.)\1{3,}\s*$")



@dataclass
class TextLine:
    """带版面坐标的一行文本。"""

    text: str
    top: float  # 距页面顶部距离（磅）
    bottom: float = 0.0
    x0: float = 0.0
    x1: float = 0.0

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.0)


@dataclass
class PageExtract:
    """单页解析的原始产物。"""

    page: int  # 页码，从 1 开始
    height: float  # 页面高度，单位为磅，用于计算版面比例
    raw_text: str  # 按行拼接的原始文本，用于清洗统计
    lines: list[TextLine] = field(default_factory=list)  # 带坐标的行，可能为空


@dataclass
class HeaderFooter:
    """检测到的页眉页脚，便于人工复核与调参。"""

    line: str
    count: int
    ratio: float
    position: str  # 取值：页眉或页脚

    def __str__(self) -> str:  # pragma: no cover
        return f"[{self.position}] ×{self.count}页({self.ratio:.0%}) {self.line[:48]}"


@dataclass
class LoadedPage:
    source: str  # 文件名
    file_path: str  # 绝对路径
    page: int  # 页码，从 1 开始，与阅读器显示一致
    total_pages: int
    text: str
    raw_text: str = ""  # 未清洗原文，便于对比清洗效果
    doc_type: str = ""  # 从父目录名推断的文档类别
    title: str = ""  # 文档标题，取首页首个有效行
    removed: list[str] = field(default_factory=list)  # 被删除的行，便于人工核查

    @property
    def char_count(self) -> int:
        return len(self.text)

    def metadata(self, page_label: str | None = None) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "source": self.source,
            "file_name": self.source,
            "file_path": self.file_path,
            "page": self.page,
            "page_label": page_label or str(self.page),
            "total_pages": self.total_pages,
            "doc_type": self.doc_type or "unknown",
        }
        if self.title:
            meta["title"] = self.title
        return meta


@dataclass
class LoadReport:
    """批量加载报告，供命令行打印。"""

    files: int = 0
    pages: int = 0
    empty_pages: int = 0
    chars: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    header_footers: dict[str, list[HeaderFooter]] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"{self.files} 个 PDF / {self.pages} 页（空页 {self.empty_pages}）"
            f" / {self.chars:,} 字符 / 跳过 {len(self.skipped)}"
        )



def _normalize_lines(text: str) -> list[str]:
    """统一换行符，去掉行尾空白与零宽字符。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return [line.rstrip() for line in text.split("\n")]


def is_page_number_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 24:
        return False
    return any(p.match(stripped) for p in _PAGE_NUMBER_PATTERNS)


def _is_noise_line(line: str) -> bool:
    """判断是否为重复符号等无意义行，如表格框线与装饰线。"""
    return bool(_REPEAT_NOISE_RE.match(line.strip())) if line.strip() else False


def clean_page_text(
    text: str,
    *,
    drop_lines: Iterable[str] = (),
    remove_page_number: bool = True,
    strip_cjk_spaces: bool = True,
    strip_whitespace: bool = True,
) -> tuple[str, list[str]]:
    """单页文本清洗。

    依次执行统一换行并去行尾空白、删除页眉页脚与页码行、删除重复符号噪音行、删除中文之间的多余空格、压缩连续空行，顺序会影响结果。返回清洗后的文本与被删除的行列表。
    """
    drop = {ln.strip() for ln in drop_lines if ln and ln.strip()}
    kept: list[str] = []
    removed: list[str] = []

    for line in _normalize_lines(text):
        stripped = line.strip()

        if not stripped:
            kept.append("")
            continue
        if stripped in drop:
            removed.append(stripped)
            continue
        if remove_page_number and is_page_number_line(stripped):
            removed.append(stripped)
            continue
        if _is_noise_line(stripped):
            removed.append(stripped)
            continue

        kept.append(stripped if strip_whitespace else line)

    out = "\n".join(kept)
    if strip_cjk_spaces:
        out = _CJK_SPACE_RE.sub("", out)
    # 连续三个以上换行压缩为两个，保留段落结构供递归切分使用
    out = re.sub(r"\n{3,}", "\n\n", out)
    if strip_whitespace:
        out = "\n".join(ln.strip() for ln in out.split("\n"))
    out = out.strip()
    return out, removed



def _in_zone(top: float, page_height: float, zone_ratio: float, zone: str) -> bool:
    """判断某行的垂直位置是否落在页面顶部或底部区域内。"""
    if page_height <= 0:
        return False
    if zone == "header":
        return top <= page_height * zone_ratio
    return top >= page_height * (1.0 - zone_ratio)


def detect_headers_footers(
    extracts: Sequence[PageExtract],
    *,
    min_ratio: float = 0.5,
    header_zone_ratio: float = 0.08,
    footer_zone_ratio: float = 0.12,
    margin: int = 3,
    max_line_length: int = 60,
    extra_patterns: Iterable[str] = (),
) -> list[HeaderFooter]:
    """按版面垂直位置与跨页重复性识别页眉页脚。

    判定条件为行位于顶部或底部区域内、跨页出现比例达到阈值、长度不超过上限。页眉区取页面高度的百分之八而非百分之十五：页眉通常贴近顶端，而正文首个章节标题从百分之八左右开始，放宽会把章节标题与首段正文纳入判定区，跨页重复时会被误删。页脚区留百分之十二，因为页码与脚注常有多行。注意，各阈值的默认值仅在该函数内生效，调用方传入配置值。
    """
    n = len(extracts)
    found: list[HeaderFooter] = []
    if n < 3:
        return found

    has_coords = any(ex.lines for ex in extracts)
    header_counter: Counter[str] = Counter()
    footer_counter: Counter[str] = Counter()

    for ex in extracts:
        if has_coords and ex.lines:
            head, tail = [], []
            for ln in ex.lines:
                stripped = ln.text.strip()
                if not stripped or _is_noise_line(stripped) or is_page_number_line(stripped):
                    continue
                if _in_zone(ln.top, ex.height, header_zone_ratio, "header"):
                    head.append(stripped)
                elif _in_zone(ln.top, ex.height, footer_zone_ratio, "footer"):
                    tail.append(stripped)
        else:
            # 无坐标时退化为按文本顺序取首尾行
            lines = [ln.strip() for ln in _normalize_lines(ex.raw_text)]
            lines = [ln for ln in lines if ln and not _is_noise_line(ln)]
            if not lines:
                continue
            head = [ln for ln in lines[:margin] if not is_page_number_line(ln)]
            tail = [ln for ln in lines[-margin:] if not is_page_number_line(ln)]

        header_counter.update(set(head))
        footer_counter.update(set(tail))

    def _collect(counter: Counter[str], position: str) -> None:
        for line, count in counter.items():
            ratio = count / n
            if ratio >= min_ratio and len(line) <= max_line_length:
                found.append(HeaderFooter(line, count, ratio, position))

    _collect(header_counter, "header")
    _collect(footer_counter, "footer")

    # 额外指定的固定页眉页脚文字，不受长度限制
    for pattern in extra_patterns:
        try:
            rx = re.compile(pattern)
        except re.error:
            continue
        for ex in extracts[: min(n, 5)]:
            candidates = [ln.text.strip() for ln in ex.lines] if ex.lines else _normalize_lines(ex.raw_text)
            for ln in candidates[: margin * 3]:
                if ln and rx.search(ln):
                    found.append(HeaderFooter(ln, n, 1.0, "header"))

    uniq: dict[tuple[str, str], HeaderFooter] = {}
    for hf in found:
        key = (hf.position, hf.line)
        if key not in uniq or uniq[key].count < hf.count:
            uniq[key] = hf
    return sorted(uniq.values(), key=lambda h: (-h.count, h.position, h.line))



# 句末标点，句子结束时不应与下一行合并
_SENTENCE_END = "。！？；：!?;:.…"


def _lines_to_text(
    lines: Sequence[TextLine],
    *,
    join_wrapped: bool = True,
    gap_ratio: float = 1.6,
    indent_tolerance: float = 2.0,
    indent_ratio: float = 1.5,
) -> str:
    """按阅读顺序把行拼回整页文本，并把硬折行的视觉行合并成逻辑段落。"""
    usable = [ln for ln in lines if ln.text.strip()]
    if not usable:
        return ""
    if not join_wrapped:
        return "\n".join(ln.text.strip() for ln in usable)

    parts: list[str] = [usable[0].text.strip()]
    prev = usable[0]
    for ln in usable[1:]:
        text = ln.text.strip()
        # 行高阈值统一取相邻行间距，标题行的高度由提取器给得偏大，直接使用不稳定
        height = prev.height or (prev.bottom - prev.top) or 1.0
        gap = ln.top - prev.bottom
        more_indented = ln.x0 > prev.x0 * indent_ratio + indent_tolerance

        prev_text = prev.text.strip()
        prev_ends_sentence = prev_text.endswith(tuple(_SENTENCE_END))

        # 新单元标记仅在上一行已结束句子时才代表新单元；上一行明显未完时，下一行无论以什么开头都是它的续行
        starts_new_unit = prev_ends_sentence and bool(_CLAUSE_START_RE.match(text))

        if (
            not prev_ends_sentence
            and gap < height * gap_ratio
            and not more_indented
            and not starts_new_unit
        ):
            # 同一段落内的折行直接拼接，不加分隔符
            parts[-1] += text
        elif gap >= height * gap_ratio:
            parts.append("")
            parts.append(text)
        else:
            parts.append(text)
        prev = ln

    text = "\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _join_kwargs(chunk_cfg: Any) -> dict[str, float]:
    """从配置中取出段落重组参数。"""
    return {
        "gap_ratio": chunk_cfg.wrap_gap_ratio,
        "indent_tolerance": chunk_cfg.wrap_indent_tolerance,
        "indent_ratio": chunk_cfg.wrap_indent_ratio,
    }


def _extract_with_pdfplumber(path: Path, cfg: PDFSettings, join_kwargs: dict[str, float] | None = None) -> list[PageExtract]:
    """按 pdfplumber 抽页，同时取文本与词级坐标，对中文排版与表格友好。"""
    import pdfplumber

    jk = join_kwargs or {}
    pages: list[PageExtract] = []
    with pdfplumber.open(str(path)) as pdf:
        for idx, page in enumerate(pdf.pages):
            lines: list[TextLine] = []
            try:
                for item in page.extract_text_lines(
                    x_tolerance=cfg.line_tolerance,
                    y_tolerance=cfg.line_tolerance,
                    use_text_flow=cfg.use_text_flow,
                ):
                    text = (item.get("text") or "").strip()
                    if text:
                        lines.append(
                            TextLine(
                                text=text,
                                top=float(item.get("top", 0.0)),
                                bottom=float(item.get("bottom", 0.0)),
                                x0=float(item.get("x0", 0.0)),
                                x1=float(item.get("x1", 0.0)),
                            )
                        )
            except Exception:  # noqa: BLE001 - 版面分析失败时退化为纯文本
                lines = []
            # 按上边界排序，不受提取器内部顺序影响
            lines.sort(key=lambda ln: (round(ln.top, 1), ln.x0))

            if lines:
                raw = _lines_to_text(lines, **jk)
            else:
                raw = page.extract_text(
                    x_tolerance=cfg.line_tolerance,
                    y_tolerance=cfg.line_tolerance,
                    use_text_flow=cfg.use_text_flow,
                ) or ""
            pages.append(
                PageExtract(
                    page=idx + 1,
                    height=float(page.height or 0.0),
                    raw_text=raw,
                    lines=lines,
                )
            )
    return pages


def _extract_with_pymupdf(path: Path, cfg: PDFSettings, join_kwargs: dict[str, float] | None = None) -> list[PageExtract]:
    """用 PyMuPDF 抽页，速度更快，作为可选后端。"""
    try:
        import fitz  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PDF engine 设为 pymupdf，但未安装 PyMuPDF。请执行：\n"
            "    uv pip install pymupdf\n"
            "或将 config.pdf.engine 改回 'pdfplumber'"
        ) from exc

    jk = join_kwargs or {}
    pages: list[PageExtract] = []
    with fitz.open(str(path)) as doc:
        for idx, page in enumerate(doc):
            height = float(page.rect.height)
            lines: list[TextLine] = []
            try:
                data = page.get_text("dict", sort=True)
                for block in data.get("blocks", []):
                    for line in block.get("lines", []):
                        text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                        if not text:
                            continue
                        x0, top, x1, bottom = line.get("bbox", (0, 0, 0, 0))
                        lines.append(
                            TextLine(text=text, top=float(top), bottom=float(bottom),
                                     x0=float(x0), x1=float(x1))
                        )
            except Exception:  # noqa: BLE001
                lines = []
            lines.sort(key=lambda ln: (round(ln.top, 1), ln.x0))
            raw = _lines_to_text(lines, **jk) if lines else (page.get_text("text", sort=True) or "")
            pages.append(PageExtract(page=idx + 1, height=height, raw_text=raw, lines=lines))
    return pages


def _extract_pages(path: Path, cfg: PDFSettings, join_kwargs: dict[str, float] | None = None) -> list[PageExtract]:
    if cfg.engine == "pymupdf":
        return _extract_with_pymupdf(path, cfg, join_kwargs)
    return _extract_with_pdfplumber(path, cfg, join_kwargs)



def load_pdf(
    pdf_path: str | Path,
    cfg: Any = _DEFAULT,
    pdf_cfg: Any = _DEFAULT,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[LoadedPage], list[HeaderFooter]]:
    """加载单个 PDF，返回清洗后的逐页文本与检测到的页眉页脚。

    清洗参数与解析参数均默认取全局配置；进度回调接收一行描述文本。返回页面列表与页眉页脚列表。
    """
    chunk_cfg = settings.chunk if isinstance(cfg, _Default) else cfg
    pcfg = settings.pdf if isinstance(pdf_cfg, _Default) else pdf_cfg

    path = Path(pdf_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")

    extracts = _extract_pages(path, pcfg, _join_kwargs(chunk_cfg))
    total = len(extracts)

    # 1 在原始提取结果上识别页眉页脚，此处需要坐标
    headers: list[HeaderFooter] = []
    if chunk_cfg.clean_headers_footers:
        headers = detect_headers_footers(
            extracts,
            min_ratio=chunk_cfg.header_footer_min_ratio,
            header_zone_ratio=chunk_cfg.header_zone_ratio,
            footer_zone_ratio=chunk_cfg.footer_zone_ratio,
            margin=chunk_cfg.header_footer_margin,
            max_line_length=chunk_cfg.header_footer_max_length,
            extra_patterns=chunk_cfg.extra_header_footer_patterns,
        )
    drop_lines = [h.line for h in headers]

    # 2 清洗
    doc_type = path.parent.name if path.parent.name != "raw" else "unknown"
    pages: list[LoadedPage] = []
    for ex in extracts:
        cleaned, removed = clean_page_text(
            ex.raw_text,
            drop_lines=drop_lines,
            remove_page_number=chunk_cfg.remove_page_number,
            strip_cjk_spaces=chunk_cfg.strip_cjk_spaces,
            strip_whitespace=chunk_cfg.strip_whitespace,
        )
        pages.append(
            LoadedPage(
                source=path.name,
                file_path=str(path),
                page=ex.page,
                total_pages=total,
                text=cleaned,
                raw_text=ex.raw_text,
                doc_type=doc_type,
                removed=removed,
            )
        )
        if progress:
            progress(f"  {path.name} 第 {ex.page}/{total} 页，清洗后 {len(cleaned)} 字符")

    # 3 拆开粘连行，让章节与条款边界重新成为分块断点
    for pg in pages:
        pg.text = split_merged_headings(pg.text)
        pg.text = split_inline_clauses(pg.text)

    # 4 标题从首页推断后回填所有页，保证同一文档内标题一致
    first_text = next((p.text for p in pages if p.text), "")
    title = pick_title(first_text, fallback=path.stem)
    for pg in pages:
        pg.title = title

    return pages, headers


# 公文常把章节标题排成字符间带空格的样式，匹配标题前先去掉空白
_WS_RE = re.compile(r"\s+")


def _compact(line: str) -> str:
    """去掉所有空白，用于标题模式的稳健匹配，返回值不影响原行。"""
    return _WS_RE.sub("", line)


# 疑似章节标题，兼容数字编号、第几章与中文序号等写法
_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[.、]|第[一二三四五六七八九十百千零\d]+[章节部分篇条]|[一二三四五六七八九十]+[、.])"
)

# 用于拆分标题与正文粘连的行，只包含编号型与章节型标题，刻意不含条款型标题：条款正文常到几十字后才出现第一个标点，按逗号切会把一条完整法规截断，条款边界交给专门的函数处理
_MERGE_SPLIT_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[.、]|第[一二三四五六七八九十百千零\d]+[章节部分篇]|[一二三四五六七八九十]+[、.])"
)

# 章节或条款编号本身，用于判断某行是章节名还是文档标题
_CHAPTER_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百千零\d]+[章节部分篇]")

# 法规与制度类文档的单元起始标记，位于行首，这些行即使不以标点结尾也不是上一行的续行
_CLAUSE_START_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百千零\d]+\s*[条条款项章节编]"   # 形如 第十三条 或 第 3 条
    r"|[（(]\s*[一二三四五六七八九十\d]+\s*[）)]"                # 形如 （一） 或 (1)
    r"|\d+(?:\.\d+)*\s*[.、)]"                                  # 形如 1. 或 1.1 或 1)
    r"|[一二三四五六七八九十]+\s*[、.]"                          # 形如一、
    r")"
)

# 出现在行中间的条款起始标记。条款密排的文档提取后常把多条法规连成一行，需要把其后的条款切到新行，否则一个块里塞进多条法规，既污染语义也让引用定位失准
_CLAUSE_INLINE_RE = re.compile(
    r"(?<=[。；;])"
    r"(第\s*[一二三四五六七八九十百千零\d]+\s*[条条款项])"
    r"(?!\s*[）)])"
    r"(?=\S)"
)


def split_inline_clauses(text: str) -> str:
    """把行中间出现的条款标记切到行首。

    只在句末标点之后、且标记后紧接非空白字符时切分，避免误伤正文中对条款的引用。
    """
    return _CLAUSE_INLINE_RE.sub(r"\n\1", text)


def _looks_like_heading(line: str) -> bool:
    """判断某行是否像章节标题，对字符间带空格的排版同样有效。"""
    return bool(_HEADING_RE.match(_compact(line)))


def _looks_like_chapter(line: str) -> bool:
    """判断某行是否为章节编号行。"""
    return bool(_CHAPTER_PREFIX_RE.match(_compact(line)))


def split_merged_headings(text: str, max_heading_length: int = 30) -> str:
    """把标题与正文粘在同一行的内容拆成两行。

    提取器常把编号型章节标题与紧随的正文并成一行，不拆分既会影响标题抽取，也让分块失去一个天然语义断点。仅当行首匹配编号型或章节型标题、且标题部分长度合理时拆分，条款标记已刻意排除。
    """
    out: list[str] = []
    for line in text.split("\n"):
        if len(line) <= max_heading_length:
            out.append(line)
            continue
        if not _MERGE_SPLIT_HEADING_RE.match(_compact(line)):
            out.append(line)
            continue
        # 标题部分取到第一个句末标点或合理长度为止
        head_end = -1
        for punct in ("。", "，", "、", "：", "；", "？", "！", ".", ",", ";"):
            pos = line.find(punct)
            if pos != -1 and (head_end == -1 or pos < head_end):
                head_end = pos
        if head_end == -1 or head_end > max_heading_length:
            head_end = min(len(line), max_heading_length)
        heading = line[:head_end].strip()
        rest = line[head_end:].strip()
        if 3 <= len(heading) <= max_heading_length and rest:
            out.append(heading)
            out.append(rest)
        else:
            out.append(line)
    return "\n".join(out)


# 中文公文的文档名后缀，用于切开文档名与发文字号粘连的行
_DOC_NAME_SUFFIXES = (
    "实施办法", "管理办法", "暂行规定", "暂行办法", "实施细则", "指导意见",
    "规定", "办法", "条例", "细则", "章程", "制度", "规则", "准则",
    "通知", "决定", "意见", "方案", "标准", "指南", "手册", "公约",
)


def _trim_doc_title(line: str) -> str:
    """从可能粘连的行中截出文档名。

    用几何间距区分文档名与发文字号并不可靠，字号不同或间距接近行高时会误合并；改用语言特征：文档名必然以规定、办法、条例、通知等后缀结尾，在后缀后截断即可。
    """
    compact = _WS_RE.sub("", line)
    if len(compact) <= 16:
        return line
    best = -1
    for suffix in _DOC_NAME_SUFFIXES:
        pos = compact.find(suffix)
        if pos != -1:
            end = pos + len(suffix)
            if best == -1 or end < best:
                best = end
    if 4 <= best <= 30:
        # 把截断位置映射回可能含空格的原串
        count = 0
        for idx, ch in enumerate(line):
            if not ch.isspace():
                count += 1
            if count == best:
                return line[: idx + 1].strip()
    return line


def pick_title(cleaned_first_page: str, fallback: str = "") -> str:
    """从首页文本中挑选文档标题。

    公文结构依次是文档标题、发文字号、章节正文，简单地取第一个长度适中的行往往挑出章节名。启发式按优先级判断：靠前的行优先，标题必然位于首页最开头；排除以章节编号开头的行；排除以句末标点结尾、含逗号或过长的正文；同位置再比较是否像标题以及长度是否适中。找不到时返回默认值，通常是文件名。
    """
    lines = [ln.strip() for ln in cleaned_first_page.split("\n") if ln.strip()]
    if not lines:
        return fallback

    # 先切开文档名与发文字号粘连的行，避免标题带上一串文号
    lines[0] = _trim_doc_title(lines[0])

    scored: list[tuple[tuple[int, int, int], str]] = []
    for idx, line in enumerate(lines[:8]):
        if line[-1] in "。！？；.!?;":
            continue
        if not (3 <= len(line) <= 40):
            continue
        if _looks_like_chapter(line):
            continue

        position_score = 3 if idx == 0 else (2 if idx <= 2 else 1)
        heading_bonus = 1 if _looks_like_heading(line) else 0
        noise = 1 if ("," in line or "，" in line) else 0
        length_score = 2 if 4 <= len(line) <= 24 else 1
        scored.append(((position_score, heading_bonus - noise, length_score), line))

    if not scored:
        return fallback
    scored.sort(key=lambda item: (item[0][0], item[0][1], item[0][2], len(item[1])), reverse=True)
    return scored[0][1]


def iter_pdf_files(raw_dir: str | Path | None = None) -> list[Path]:
    """列出待入库的 PDF，递归查找原始目录并按路径排序以保证结果可复现。"""
    root = Path(raw_dir) if raw_dir else settings.paths.raw_dir
    if not root.exists():
        return []
    return sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
        key=lambda p: str(p).lower(),
    )


def load_pdfs(
    raw_dir: str | Path | None = None,
    cfg: Any = _DEFAULT,
    pdf_cfg: Any = _DEFAULT,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[LoadedPage], LoadReport]:
    """批量加载原始目录下的全部 PDF，跳过无效文件开关为真时跳过并记入报告。"""
    chunk_cfg = settings.chunk if isinstance(cfg, _Default) else cfg
    pcfg = settings.pdf if isinstance(pdf_cfg, _Default) else pdf_cfg

    files = iter_pdf_files(raw_dir)
    report = LoadReport(files=len(files))
    all_pages: list[LoadedPage] = []

    for path in files:
        try:
            pages, headers = load_pdf(path, chunk_cfg, pcfg, progress=progress)
        except Exception as exc:  # noqa: BLE001
            if not pcfg.skip_invalid:
                raise
            report.skipped.append((path.name, f"{type(exc).__name__}: {exc}"))
            if progress:
                progress(f"  [跳过] {path.name}: {exc}")
            continue

        report.header_footers[path.name] = headers
        for pg in pages:
            if pg.text:
                report.pages += 1
                report.chars += pg.char_count
            else:
                report.empty_pages += 1
        all_pages.extend(pages)

    if not pcfg.keep_empty_pages:
        all_pages = [p for p in all_pages if p.text]

    return all_pages, report


def to_documents(pages: Sequence[LoadedPage], cfg: Any = _DEFAULT):
    """转为文档对象，供分块使用；保留哪些元数据字段由配置决定。"""
    from langchain_core.documents import Document

    chunk_cfg = settings.chunk if isinstance(cfg, _Default) else cfg
    fields = set(chunk_cfg.metadata_fields)

    docs: list[Document] = []
    for pg in pages:
        meta = {k: v for k, v in pg.metadata().items() if k in fields}
        meta.setdefault("source", pg.source)
        meta.setdefault("page", pg.page)
        docs.append(Document(page_content=pg.text, metadata=meta))
    return docs


def describe_headers_footers(headers: Sequence[HeaderFooter]) -> str:
    """把页眉页脚检测结果渲染为可读文本，供命令行与人工复核使用。"""
    if not headers:
        return "（未检测到页眉页脚）"
    return "\n".join(f"  {hf}" for hf in headers)
