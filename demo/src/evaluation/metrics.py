"""检索指标，包括召回率、命中率、平均倒数排名、准确率与归一化折损累计增益。

召回率的分母是相关文档总数，相关文档多于一个时只命中其中一个只算部分召回；命中率只判断是否至少命中一个。两者同时报告才能区分漏检与未找到。混合后精排策略只返回精排保留条数，其较大截断位置的召回率必然等于精排保留位置的召回率，这是策略特性而非缺陷，精排价值体现在首位与前列的指标上。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence



def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """
    召回率，即前若干条中命中的相关文档数除以相关文档总数。

    前若干条指按相关性降序排列的检索结果；相关文档为空或截断位置非正时返回零。
    """
    rel = set(relevant)
    if not rel or k <= 0:
        return 0.0
    top = set(retrieved[:k])
    return len(top & rel) / len(rel)


def hit_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """命中率，前若干条中至少命中一个相关文档为 1，否则为 0，多跳题据此判断是否找到任何依据。"""
    rel = set(relevant)
    if not rel or k <= 0:
        return 0.0
    return 1.0 if set(retrieved[:k]) & rel else 0.0


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str], k: int | None = None) -> float:
    """首个命中位置的名次倒数，未命中返回 0。"""
    rel = set(relevant)
    if not rel:
        return 0.0
    limit = len(retrieved) if k is None else min(k, len(retrieved))
    for i, cid in enumerate(retrieved[:limit], 1):
        if cid in rel:
            return 1.0 / i
    return 0.0


def precision_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """准确率，即前若干条中相关文档的占比，可反映送入大模型的上下文中噪声的比例。"""
    if k <= 0:
        return 0.0
    top = list(retrieved[:k])
    if not top:
        return 0.0
    rel = set(relevant)
    return sum(1 for cid in top if cid in rel) / len(top)


def dcg_at_k(gains: Sequence[float], k: int) -> float:
    """
    折损累计增益，按名次对增益值做对数折损后求和，名次从 1 开始。

    标准形式按相关度做指数加权，在二元相关性下指数项恒为 1，因此直接使用增益值，同时兼容二元与分级标注。
    """
    total = 0.0
    for i, gain in enumerate(gains[:k], 1):
        total += gain / math.log2(i + 1)
    return total


def ndcg_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """
    归一化折损累计增益，即实际折损累计增益除以理想排序下的折损累计增益。

    理想排序下相关文档占据最前面的位置，其数量取相关文档总数与截断位置的较小值。
    """
    rel = set(relevant)
    if not rel or k <= 0:
        return 0.0
    gains = [1.0 if cid in rel else 0.0 for cid in retrieved[:k]]
    dcg = dcg_at_k(gains, k)
    ideal_hits = min(len(rel), k)
    idcg = dcg_at_k([1.0] * ideal_hits, k)
    return dcg / idcg if idcg > 0 else 0.0



@dataclass
class ItemMetrics:
    """一条测试样本的检索指标。"""

    question: str = ""
    category: str = ""
    # 各截断位置对应的召回率
    recall: dict[int, float] = field(default_factory=dict)
    hit: dict[int, float] = field(default_factory=dict)
    precision: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    ndcg: float = 0.0
    # 首个命中结果的名次，未命中为 0
    first_hit_rank: int = 0
    n_retrieved: int = 0
    n_relevant: int = 0
    # 命中的相关编号与漏掉的相关编号，用于错误分析
    hits: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    # 单次检索耗时，单位为秒，来自检索管线的分步统计
    seconds: float = 0.0
    timing: dict[str, float] = field(default_factory=dict)

    def to_row(self, strategy: str = "") -> dict[str, Any]:
        """摊平为一行，便于写入表格或文件。"""
        row: dict[str, Any] = {
            "strategy": strategy,
            "category": self.category,
            "question": self.question,
            "n_relevant": self.n_relevant,
            "n_retrieved": self.n_retrieved,
            "first_hit_rank": self.first_hit_rank,
            "mrr": round(self.mrr, 4),
            "ndcg": round(self.ndcg, 4),
        }
        for k, v in sorted(self.recall.items()):
            row[f"recall@{k}"] = round(v, 4)
        for k, v in sorted(self.precision.items()):
            row[f"precision@{k}"] = round(v, 4)
        row["seconds"] = round(self.seconds, 3)
        return row


def evaluate_item(
    retrieved: Sequence[str],
    relevant: Sequence[str],
    *,
    question: str = "",
    category: str = "",
    recall_ks: Sequence[int] = (1, 3, 5, 10, 20),
    mrr_k: int = 10,
    ndcg_k: int = 10,
    seconds: float = 0.0,
    timing: dict[str, float] | None = None,
) -> ItemMetrics:
    """计算单条样本的全部指标。"""
    rel = list(relevant)
    rel_set = set(rel)
    first_rank = 0
    for i, cid in enumerate(retrieved, 1):
        if cid in rel_set:
            first_rank = i
            break

    return ItemMetrics(
        question=question,
        category=category,
        recall={k: recall_at_k(retrieved, rel, k) for k in recall_ks},
        hit={k: hit_at_k(retrieved, rel, k) for k in recall_ks},
        precision={k: precision_at_k(retrieved, rel, k) for k in recall_ks},
        mrr=reciprocal_rank(retrieved, rel, mrr_k),
        ndcg=ndcg_at_k(retrieved, rel, ndcg_k),
        first_hit_rank=first_rank,
        n_retrieved=len(retrieved),
        n_relevant=len(rel),
        hits=[cid for cid in retrieved if cid in rel_set],
        missed=[cid for cid in rel if cid not in set(retrieved)],
        seconds=seconds,
        timing=dict(timing or {}),
    )


def aggregate(items: Sequence[ItemMetrics], recall_ks: Sequence[int] = (1, 3, 5, 10, 20)) -> dict[str, Any]:
    """把多条样本的指标求平均，各题等权。"""
    if not items:
        return {"n": 0}
    n = len(items)
    agg: dict[str, Any] = {
        "n": n,
        "mrr": round(sum(x.mrr for x in items) / n, 4),
        "ndcg": round(sum(x.ndcg for x in items) / n, 4),
        "avg_seconds": round(sum(x.seconds for x in items) / n, 3),
        "avg_first_hit_rank": round(
            sum(x.first_hit_rank for x in items if x.first_hit_rank > 0)
            / max(sum(1 for x in items if x.first_hit_rank > 0), 1), 2
        ),
    }
    for k in recall_ks:
        agg[f"recall@{k}"] = round(sum(x.recall.get(k, 0.0) for x in items) / n, 4)
        agg[f"precision@{k}"] = round(sum(x.precision.get(k, 0.0) for x in items) / n, 4)
        agg[f"hit@{k}"] = round(sum(x.hit.get(k, 0.0) for x in items) / n, 4)
    return agg
