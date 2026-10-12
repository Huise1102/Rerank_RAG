"""阶段 3 —— 引用解析与溯源（citation.py）。

解析答案里的引用标记（``[1]``、``[1][2]``、``[1,2]``、``[1、2]``、``[1-3]``、``【1】``、
``（1）``），映射回文档块（source / page / chunk_id）：解析宽松，映射回编号严格。
``unused_contexts`` 是召回但未被引用的（可分析检索精度），``invalid_indices`` 是引用了
上下文中不存在的编号（越界引用往往是幻觉信号），两者必须分开统计。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from src.generation.prompt import PromptContext

#: 引用编号的匹配模式（宽松，容忍多种写法）
_BRACKET_RE = re.compile(
    r"[\[【〔（(]\s*(\d+(?:\s*[-~～—至到]\s*\d+)?(?:\s*[,，、;；/]\s*\d+)*)\s*[\]】〕）)]"
)

_SPLIT_RE = re.compile(r"[,，、;；/]")

#: 区间写法：1-3 / 1~3 / 1至3
_RANGE_RE = re.compile(r"(\d+)\s*[-~～—至到]\s*(\d+)")

#: 模型声称"资料不足"的常见表述（识别拒答，评估时区分"拒答"与"答错"）
_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"无法回答", r"无法确定", r"无法找到", r"无从得知",
        r"资料(?:中|里)?(?:未|没有|不)(?:提及|包含|涉及|提供|找到)",
        r"上下文(?:中|里)?(?:未|没有|不)(?:提及|包含|涉及|提供|找到)",
        r"参考(?:资料|文献)(?:中|里)?(?:未|没有|不)(?:提及|包含|涉及|提供|找到)",
        r"现有资料不足", r"资料不足", r"信息不足", r"依据不足",
        r"没有(?:相关|足够)的?(?:信息|资料|内容|依据)",
        r"未(?:能)?找到相关", r"超出(?:了)?(?:资料|知识库)范围",
        r"(?:抱歉|对不起)[,，].{0,20}(?:无法|不能)",
    )
)


@dataclass
class Citation:
    """一条引用。"""

    index: int  # 模型写的编号
    chunk_id: str = ""
    source: str = ""
    page: int | str = ""
    text: str = ""
    score: float | None = None
    count: int = 1  # 在答案中被引用的次数

    def label(self) -> str:
        """人类可读的引用标签，如 ``[1] 测试.pdf 第 18 页``。"""
        if self.page not in ("", None):
            return f"[{self.index}] {self.source} 第 {self.page} 页"
        return f"[{self.index}] {self.source}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "chunk_id": self.chunk_id,
            "source": self.source,
            "page": self.page,
            "score": self.score,
            "count": self.count,
            "label": self.label(),
            "text": self.text,
        }


@dataclass
class CitationReport:
    """一次答案的引用分析结果。"""

    citations: list[Citation] = field(default_factory=list)
    #: 送进 prompt 但未被引用的上下文
    unused_contexts: list[PromptContext] = field(default_factory=list)
    #: 引用了但上下文中不存在的编号（越界引用，往往伴随幻觉）
    invalid_indices: list[int] = field(default_factory=list)
    is_refusal: bool = False
    raw_markers: list[int] = field(default_factory=list)

    @property
    def used_indices(self) -> list[int]:
        return [c.index for c in self.citations]

    @property
    def has_citation(self) -> bool:
        return bool(self.citations)

    @property
    def coverage(self) -> float:
        """被引用的上下文占送入上下文的比例（0~1）。可反映检索精度。"""
        total = len(self.citations) + len(self.unused_contexts)
        return len(self.citations) / total if total else 0.0

    def labels(self) -> list[str]:
        return [c.label() for c in self.citations]

    def sources(self) -> list[dict[str, Any]]:
        """去重后的来源列表，便于前端做"参考资料"区块。"""
        seen: dict[str, dict[str, Any]] = {}
        for c in self.citations:
            key = f"{c.source}#{c.page}"
            if key not in seen:
                seen[key] = {"source": c.source, "page": c.page, "chunk_id": c.chunk_id,
                             "indices": [c.index], "score": c.score}
            else:
                seen[key]["indices"].append(c.index)
        return list(seen.values())

    def summary(self) -> str:
        if self.is_refusal and not self.citations:
            return "模型判定资料不足（未引用任何来源）"
        parts = [f"引用 {len(self.citations)} 条来源"]
        if self.unused_contexts:
            parts.append(f"未引用 {len(self.unused_contexts)} 条")
        if self.invalid_indices:
            parts.append(f"⚠️ 越界引用 {self.invalid_indices}")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "citations": [c.to_dict() for c in self.citations],
            "unused_indices": [c.index for c in self.unused_contexts],
            "invalid_indices": self.invalid_indices,
            "is_refusal": self.is_refusal,
            "raw_markers": self.raw_markers,
            "coverage": round(self.coverage, 4),
            "summary": self.summary(),
        }



def _expand_group(group: str) -> list[int]:
    """把一个编号组展开成编号列表：``"1,2"`` -> [1,2]；``"1-3"`` -> [1,2,3]。"""
    out: list[int] = []
    for part in _SPLIT_RE.split(group):
        part = part.strip()
        if not part:
            continue
        m = _RANGE_RE.fullmatch(part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            # 上限保护：防止模型写出 [1-9999] 把内存吃光
            if hi - lo <= 50:
                out.extend(range(lo, hi + 1))
            else:
                out.extend([lo, hi])
            continue
        if part.isdigit():
            out.append(int(part))
    return out


def scan_markers(
    text: str,
    *,
    whitelist: Iterable[int] | None = None,
) -> tuple[list[int], list[int], dict[int, int]]:
    """一次扫描得到：按首次出现顺序去重的编号、越界编号、编号->出现次数。

    计数必须基于"所有出现"；若改用 ``parse_markers`` 的去重结果，次数会恒等于 1。
    """
    allowed = set(whitelist) if whitelist is not None else None
    ordered: list[int] = []
    invalid: list[int] = []
    counts: dict[int, int] = {}

    for m in _BRACKET_RE.finditer(text):
        for num in _expand_group(m.group(1)):
            counts[num] = counts.get(num, 0) + 1
            if allowed is not None and num not in allowed:
                if num not in invalid:
                    invalid.append(num)
                continue
            if num not in ordered:
                ordered.append(num)
    return ordered, invalid, counts


def parse_markers(text: str, *, whitelist: Iterable[int] | None = None) -> tuple[list[int], list[int]]:
    """解析引用标记，去重后按首次出现顺序返回。

    :param whitelist: 允许的编号集合；给出时不在集合内的编号单独返回
    """
    ordered, invalid, _ = scan_markers(text, whitelist=whitelist)
    return ordered, invalid


def is_refusal(answer: str) -> bool:
    """判断答案是否属于"资料不足，无法回答"这类拒答。"""
    if not answer:
        return False
    return any(p.search(answer) for p in _REFUSAL_PATTERNS)


def extract_citations(
    answer: str,
    contexts: Sequence[PromptContext],
    *,
    keep_text: bool = True,
) -> CitationReport:
    """解析答案中的引用并映射回上下文。

    :param contexts: 送进 prompt 的上下文（编号必须与 prompt 中一致）
    :param keep_text: 是否保留文档块原文（评估可关掉省内存）
    """
    by_index = {ctx.index: ctx for ctx in contexts}
    # 计数必须用"所有出现"，parse_markers 的去重结果会恒为 1
    markers, invalid, counts = scan_markers(answer, whitelist=by_index.keys())

    citations: list[Citation] = []
    for idx in markers:
        ctx = by_index[idx]
        citations.append(
            Citation(
                index=idx,
                chunk_id=ctx.chunk_id,
                source=ctx.source,
                page=ctx.page,
                text=ctx.text if keep_text else "",
                score=ctx.score,
                count=counts.get(idx, 1),
            )
        )

    # 引用按答案中出现的先后排列（论证顺序），而非编号大小
    used = set(markers)
    unused = [ctx for ctx in contexts if ctx.index not in used]

    return CitationReport(
        citations=citations,
        unused_contexts=unused,
        invalid_indices=invalid,
        is_refusal=is_refusal(answer),
        raw_markers=markers,
    )


def strip_markers(answer: str, *, replacement: str = "") -> str:
    """去掉答案中的引用标记，得到纯正文（导出 Word、展示摘要用）。"""
    return _BRACKET_RE.sub(replacement, answer)


def renumber(answer: str, mapping: dict[int, int]) -> str:
    """按映射重编号引用标记（多轮对话中新旧上下文编号会冲突）。"""
    def _sub(m: re.Match[str]) -> str:
        nums = _expand_group(m.group(1))
        if not nums:
            return m.group(0)
        new_nums = [mapping.get(n) for n in nums]
        if any(n is None for n in new_nums):
            return m.group(0)
        return "[" + ",".join(str(n) for n in new_nums) + "]"  # type: ignore[arg-type]

    return _BRACKET_RE.sub(_sub, answer)
