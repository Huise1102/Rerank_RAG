"""测试集加载与校验：只读取、校验、统计，不生成任何标注内容。

``question`` 非空不重复；``relevant_chunk_ids`` 为相关块 id 列表，须存在于当前索引（抄错会让
该类 Recall 恒为 0）；``category`` ∈ 术语精确 / 语义泛化 / 多跳推理。

判定粒度：chunk 按 chunk_id（同索引内比较）；page 按「文件名+页码」（跨分块策略比较，同页多块
保序去重）—— 跨分块策略若用 chunk_id，fixed 档会全为 0。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from config import settings

#: 三个必填类别
CATEGORIES: tuple[str, ...] = ("术语精确", "语义泛化", "多跳推理")

#: 类别别名（英文/简写也接受，统一归一化到中文）
_CATEGORY_ALIASES: dict[str, str] = {
    "术语精确": "术语精确", "term": "术语精确", "exact": "术语精确", "keyword": "术语精确",
    "语义泛化": "语义泛化", "semantic": "语义泛化", "paraphrase": "语义泛化",
    "多跳推理": "多跳推理", "multi-hop": "多跳推理", "multihop": "多跳推理", "reasoning": "多跳推理",
}

#: chunk_id 形如 ``recursive:测试.pdf:p18:c0:028f89c7``
_CHUNK_ID_RE = re.compile(r"^(?P<strategy>[a-z_]+):(?P<source>.+):p(?P<page>\d+):c(?P<idx>\d+):[0-9a-f]{8}$")


def parse_chunk_id(chunk_id: str) -> tuple[str, int] | None:
    """
    从 chunk_id 解析出 ``(文件名, 页码)``。

    跨分块策略比较必须退到页码粒度：切到 fixed 索引后页码相同、chunk_id 完全不同，
    若仍按 chunk_id 比对，Recall 必然恒为 0。
    """
    m = _CHUNK_ID_RE.match(chunk_id)
    if not m:
        return None
    return m.group("source"), int(m.group("page"))



@dataclass
class TestItem:
    """一条测试样本。"""

    question: str
    relevant_chunk_ids: list[str] = field(default_factory=list)
    category: str = "语义泛化"
    note: str = ""
    #: 可选：参考答案要点，裁判评分时可作参考
    reference_answer: str = ""
    #: 原始序号（从 1 开始），报错时能精确指到第几条
    index: int = 0

    def source_pages(self) -> set[tuple[str, int]]:
        """
        标注相关块对应的 ``(文件名, 页码)`` 集合。

        用于分块策略消融：不同分块策略产生不同 chunk_id，但"答案在哪一页"是稳定的。
        """
        out: set[tuple[str, int]] = set()
        for cid in self.relevant_chunk_ids:
            parsed = parse_chunk_id(cid)
            if parsed:
                out.add(parsed)
        return out

    def page_keys(self) -> set[str]:
        """页码级判定键集合（形如 ``测试.pdf#p18``）。"""
        return {page_key(src, page) for src, page in self.source_pages()}

    def to_dict(self) -> dict[str, Any]:
        data = {
            "question": self.question,
            "relevant_chunk_ids": list(self.relevant_chunk_ids),
            "category": self.category,
        }
        if self.note:
            data["note"] = self.note
        if self.reference_answer:
            data["reference_answer"] = self.reference_answer
        return data


@dataclass
class ValidationIssue:
    """一条校验问题。"""

    index: int
    level: Literal["error", "warning"]
    message: str

    def __str__(self) -> str:
        mark = "错误" if self.level == "error" else "警告"
        return f"[{mark}] 第 {self.index} 条: {self.message}"


@dataclass
class TestSet:
    """加载好的测试集。"""

    items: list[TestItem] = field(default_factory=list)
    path: Path | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, i: int) -> TestItem:
        return self.items[i]

    @property
    def questions(self) -> list[str]:
        return [it.question for it in self.items]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [x for x in self.issues if x.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [x for x in self.issues if x.level == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors and bool(self.items)

    def by_category(self) -> dict[str, list[TestItem]]:
        grouped: dict[str, list[TestItem]] = {c: [] for c in CATEGORIES}
        for it in self.items:
            grouped.setdefault(it.category, []).append(it)
        return {k: v for k, v in grouped.items() if v}

    def category_counts(self) -> dict[str, int]:
        return dict(Counter(it.category for it in self.items))

    def summary(self) -> str:
        if not self.items:
            return "测试集为空"
        counts = self.category_counts()
        dist = " / ".join(f"{k} {v}" for k, v in counts.items())
        ids = sum(len(it.relevant_chunk_ids) for it in self.items)
        return (
            f"{len(self.items)} 条（{dist}）｜ 标注文档块 {ids} 个"
            + (f" ｜ {len(self.errors)} 个错误" if self.errors else "")
            + (f" ｜ {len(self.warnings)} 个警告" if self.warnings else "")
        )



def find_testset_file(path: str | Path | None = None) -> Path:
    """定位测试集文件（显式路径 > data/testset.json > data/eval/testset.json）。"""
    if path is not None:
        return Path(path)
    return settings.evaluation.resolve_testset_file()


def _normalize_category(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    return _CATEGORY_ALIASES.get(key, _CATEGORY_ALIASES.get(str(raw or "").strip(), ""))


def load_testset(
    path: str | Path | None = None,
    *,
    validate: bool = True,
    check_chunk_ids: bool = True,
) -> TestSet:
    """
    加载并校验测试集。

    :param validate: 是否做结构校验
    :param check_chunk_ids: 是否校验 chunk_id 真实存在于当前索引。建议开启 —— 手抄 id
        出错会让 Recall 永远为 0，且看起来像"检索效果差"，极难排查
    :returns: TestSet
    :raises FileNotFoundError: 文件不存在（附带标注指引）
    """
    target = find_testset_file(path)
    if not target.exists():
        raise FileNotFoundError(_missing_testset_hint(target))

    raw = json.loads(target.read_text(encoding="utf-8"))

    # 兼容两种顶层结构：裸数组，或 {"items": [...]}
    if isinstance(raw, dict):
        raw_items = raw.get("items") or raw.get("questions") or []
    elif isinstance(raw, list):
        raw_items = raw
    else:
        raise ValueError(f"测试集顶层应为数组或含 items 的对象，实际是 {type(raw).__name__}")

    items: list[TestItem] = []
    issues: list[ValidationIssue] = []

    for i, entry in enumerate(raw_items, 1):
        if not isinstance(entry, dict):
            issues.append(ValidationIssue(i, "error", f"条目应为对象，实际是 {type(entry).__name__}"))
            continue

        question = str(entry.get("question", "")).strip()
        raw_ids = entry.get("relevant_chunk_ids") or entry.get("chunk_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        ids = [str(x).strip() for x in raw_ids if str(x).strip()]

        category = _normalize_category(entry.get("category"))
        item = TestItem(
            question=question,
            relevant_chunk_ids=ids,
            category=category or "语义泛化",
            note=str(entry.get("note", "")).strip(),
            reference_answer=str(entry.get("reference_answer", "")).strip(),
            index=i,
        )
        items.append(item)

        if not question:
            issues.append(ValidationIssue(i, "error", "question 为空"))
        if not ids:
            issues.append(ValidationIssue(i, "error", "relevant_chunk_ids 为空"))
        if not category:
            issues.append(ValidationIssue(
                i, "warning",
                f"category 缺失或无法识别（{entry.get('category')!r}），已按「语义泛化」处理；"
                f"可选：{' / '.join(CATEGORIES)}",
            ))
        for cid in ids:
            if not _CHUNK_ID_RE.match(cid):
                issues.append(ValidationIssue(
                    i, "warning",
                    f"chunk_id 格式可疑：{cid!r}（正常形如 recursive:文件名:p18:c0:028f89c7）",
                ))

    seen: dict[str, int] = {}
    for item in items:
        if item.question in seen:
            issues.append(ValidationIssue(
                item.index, "warning", f"问题与第 {seen[item.question]} 条重复：{item.question[:30]}"
            ))
        else:
            seen[item.question] = item.index

    testset = TestSet(items=items, path=target, issues=issues)

    if validate and check_chunk_ids:
        testset.issues.extend(check_against_index(testset))

    return testset


def _missing_testset_hint(target: Path) -> str:
    return (
        f"未找到测试集文件：{target}\n\n"
        "阶段 5 需要你手动标注测试集。步骤如下：\n"
        "  1) 生成标注候选（推荐，含每块的 chunk_id 与摘要）：\n"
        "       python -m src.evaluation.cli candidates --out data/testset.draft.json\n"
        "  2) 打开生成的文件，为每个问题挑选正确的 chunk_id，删除多余条目\n"
        "  3) 保存为 data/testset.json（或 data/eval/testset.json，两处都会被读取）\n"
        "  4) 校验：python -m src.evaluation.cli check\n\n"
        "格式：\n"
        '  [{"question": "…", "relevant_chunk_ids": ["recursive:文件名:p18:c0:028f89c7"], '
        '"category": "术语精确"}]'
    )



def indexed_chunk_ids(chunk_strategy: str | None = None, limit: int | None = None) -> set[str]:
    """读取当前索引中实际存在的 chunk_id 集合。"""
    from src.ingest import splitter

    strategy = chunk_strategy or settings.chunk.default_strategy
    result = splitter.load_chunks(strategy)
    ids = {c.chunk_id for c in result.chunks}
    return set(list(ids)[:limit]) if limit else ids


def check_against_index(testset: TestSet, chunk_strategy: str | None = None) -> list[ValidationIssue]:
    """
    校验测试集里的 chunk_id 是否真实存在于索引。

    抄错一个字符就会让该类问题的 Recall 恒为 0，表面上像"检索效果差"，实际是标注错误。
    """
    try:
        valid = indexed_chunk_ids(chunk_strategy)
    except Exception as exc:  # noqa: BLE001 - 索引未就绪不应让加载失败
        return [ValidationIssue(0, "warning", f"无法读取索引以校验 chunk_id：{exc}")]

    if not valid:
        return [ValidationIssue(0, "warning", "索引为空，跳过长 chunk_id 校验（请先完成数据入库）")]

    issues: list[ValidationIssue] = []
    for item in testset.items:
        missing = [cid for cid in item.relevant_chunk_ids if cid not in valid]
        if missing:
            issues.append(ValidationIssue(
                item.index, "error",
                f"以下 chunk_id 不在当前索引中（共 {len(missing)} 个）：{missing[:2]}"
                + ("…" if len(missing) > 2 else "")
                + "。请检查是否抄写错误，或索引是否已重建（重建后 chunk_id 会变化）",
            ))
    return issues


def page_key(source: str, page: int | str) -> str:
    """生成「文件+页码」形式的标注键，便于人工标注。"""
    return f"{source}#p{page}"


def resolve_by_page(
    source_page_pairs: Sequence[tuple[str, int | str]],
    chunk_strategy: str | None = None,
) -> list[str]:
    """
    把「文件名 + 页码」形式的标注转换为 chunk_id 列表。

    人工标注用页码最省事（不用复制长 id）。注意一个页面对应多个块，该页所有块都会
    算作相关 —— 比精确到块宽松，但比手抄 id 可靠，适合快速起步。
    """
    from src.ingest import splitter

    strategy = chunk_strategy or settings.chunk.default_strategy
    result = splitter.load_chunks(strategy)
    wanted = {(str(s).strip(), str(p).strip()) for s, p in source_page_pairs}
    return [
        c.chunk_id for c in result.chunks
        if (str(c.metadata.get("source", "")), str(c.metadata.get("page", ""))) in wanted
    ]



def save_testset(testset: TestSet | Sequence[TestItem], path: str | Path | None = None) -> Path:
    """把测试集写回 JSON（便于程序化生成后人工润色）。"""
    target = Path(path) if path else find_testset_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    items = testset.items if isinstance(testset, TestSet) else list(testset)
    payload = [it.to_dict() for it in items]
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def format_issues(testset: TestSet, limit: int = 20) -> str:
    """把校验问题渲染成可读文本。"""
    if not testset.issues:
        return "校验通过，未发现问题。"
    lines = []
    for issue in testset.issues[:limit]:
        lines.append("  " + str(issue))
    if len(testset.issues) > limit:
        lines.append(f"  …还有 {len(testset.issues) - limit} 条")
    return "\n".join(lines)



def make_annotation_candidates(
    chunk_strategy: str | None = None,
    *,
    per_file: int = 0,
    max_chars: int = 160,
) -> list[dict[str, Any]]:
    """
    生成标注候选项：把语料按「文件 + 页码」分组，列出每块的 chunk_id 与摘要。

    ``question`` 留空、``relevant_chunk_ids`` 预填该页全部块；哪些块与哪个问题相关仍由人工
    判断。``per_file`` 为每个文件最多列出的块数（0 表示全部），``max_chars`` 为摘要上限。
    """
    from src.ingest import splitter

    strategy = chunk_strategy or settings.chunk.default_strategy
    result = splitter.load_chunks(strategy)

    grouped: dict[tuple[str, str], list[Any]] = {}
    for chunk in result.chunks:
        key = (str(chunk.metadata.get("source", "")), str(chunk.metadata.get("page", "")))
        grouped.setdefault(key, []).append(chunk)

    candidates: list[dict[str, Any]] = []
    counter: Counter[str] = Counter()
    for (source, page), chunks in sorted(grouped.items(), key=lambda kv: (kv[0][0], int(kv[0][1]) if kv[0][1].isdigit() else 0)):
        if per_file and counter[source] >= per_file:
            continue
        counter[source] += 1
        text = chunks[0].text.replace("\n", " ")
        candidates.append({
            "_提示": "把 question 改写成真实提问；relevant_chunk_ids 保留真正相关的块，删除其余；"
                     f"category 从 {list(CATEGORIES)} 中选一个",
            "question": "",
            "relevant_chunk_ids": [c.chunk_id for c in chunks],
            "category": "语义泛化",
            "_source": source,
            "_page": page,
            "_preview": text[:max_chars] + ("…" if len(text) > max_chars else ""),
        })
    return candidates


def save_annotation_candidates(
    path: str | Path = "data/testset.draft.json",
    **kwargs: Any,
) -> Path:
    """把标注候选项写入文件（默认 ``data/testset.draft.json``）。"""
    target = Path(path)
    if not target.is_absolute():
        target = settings.paths.data_dir.parent / target
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "_说明": (
            "这是标注草稿，不是测试集。请：① 为每行写一个真实问题；"
            "② 只保留真正相关的 chunk_id；③ 选定 category；"
            "④ 把需要的条目复制到 data/testset.json（顶层必须是数组，且**不要**带 _ 开头的字段）"
        ),
        "_生成时间": datetime.now().isoformat(timespec="seconds"),
        "items": make_annotation_candidates(**kwargs),
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
