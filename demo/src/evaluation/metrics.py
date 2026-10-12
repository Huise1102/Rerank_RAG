"""检索指标：Recall@k、Hit@k、MRR、Precision@k、nDCG@k。

Recall@k 的分母是相关文档总数，相关文档多于一个时命中其中一个只算部分召回；
Hit@k 只问是否至少命中一个。两者一起报才能区分"漏没漏"和"有没有找到"。

``hybrid_rerank`` 只返回 ``rerank.top_k`` 条，其 Recall@10/@20 必然等于 Recall@5，
这是策略特性不是 bug，精排的价值体现在 Recall@1/@5 与 MRR/nDCG 上。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence



def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """
    Recall@k：前 k 条中命中的相关文档数 / 相关文档总数。

    :param retrieved: 检索返回的 chunk_id，按相关性降序
    :param relevant: 标注的相关 chunk_id 集合
    :param k: 截断位置；relevant 为空或 k<=0 时返回 0.0
    """
    rel = set(relevant)
    if not rel or k <= 0:
        return 0.0
    top = set(retrieved[:k])
    return len(top & rel) / len(rel)


def hit_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Hit@k：前 k 条命中至少一个相关文档则为 1，否则 0。多跳题看"有没有找到任何依据"。"""
    rel = set(relevant)
    if not rel or k <= 0:
        return 0.0
    return 1.0 if set(retrieved[:k]) & rel else 0.0


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str], k: int | None = None) -> float:
    """MRR 的单项：第一个命中位置的倒数；未命中返回 0。"""
    rel = set(relevant)
    if not rel:
        return 0.0
    limit = len(retrieved) if k is None else min(k, len(retrieved))
    for i, cid in enumerate(retrieved[:limit], 1):
        if cid in rel:
            return 1.0 / i
    return 0.0


def precision_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Precision@k：前 k 条里相关文档占比（送进 LLM 的上下文有多少是噪声）。"""
    if k <= 0:
        return 0.0
    top = list(retrieved[:k])
    if not top:
        return 0.0
    rel = set(relevant)
    return sum(1 for cid in top if cid in rel) / len(top)


def dcg_at_k(gains: Sequence[float], k: int) -> float:
    """
    折损累计增益：``Σ gain_i / log2(i+1)``（i 从 1 开始）。

    标准写法 ``(2^rel - 1) / log2(i+1)`` 在二元相关性下 ``2^1-1 = 1``，
    因此直接用 gain 值，兼容二元与分级标注。
    """
    total = 0.0
    for i, gain in enumerate(gains[:k], 1):
        total += gain / math.log2(i + 1)
    return total


def ndcg_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """
    nDCG@k（二元相关性）：``DCG@k / IDCG@k``。

    理想排序下 m = min(|相关|, k) 个相关文档占据前 m 位，故 ``IDCG = Σ 1/log2(i+1)``。
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
    #: 每个 k 对应的 Recall
    recall: dict[int, float] = field(default_factory=dict)
    hit: dict[int, float] = field(default_factory=dict)
    precision: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    ndcg: float = 0.0
    #: 第一个命中结果的名次（未命中为 0）
    first_hit_rank: int = 0
    n_retrieved: int = 0
    n_relevant: int = 0
    #: 命中的相关 id 与漏掉的相关 id，用于错误分析
    hits: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    #: 本次检索耗时（秒），来自 pipeline 的分步统计
    seconds: float = 0.0
    timing: dict[str, float] = field(default_factory=dict)

    def to_row(self, strategy: str = "") -> dict[str, Any]:
        """摊平为一行，便于写入 DataFrame / CSV。"""
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
    """把多条样本的指标求平均（macro-average，即各题等权）。"""
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
