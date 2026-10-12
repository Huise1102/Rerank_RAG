"""检索策略对比评估：对每种策略跑同一测试集，输出对比表与 CSV。

四种策略：``vector``（纯语义基线）、``bm25``（纯词匹配基线）、``hybrid``（向量+BM25+RRF）、
``hybrid_rerank``（完整链路）。检索类指标不消耗 LLM token。

``hybrid_rerank`` 在 CPU 上每题约 18~21 秒（精排是瓶颈），100 题约需 30~35 分钟，建议先用
``--limit 10`` 小样本验证。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from config import settings
from src.evaluation.metrics import ItemMetrics, aggregate, evaluate_item
from src.evaluation.testset import TestItem, TestSet
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 默认对比的策略
DEFAULT_STRATEGIES: tuple[str, ...] = ("vector", "bm25", "hybrid", "hybrid_rerank")

#: 策略的中文显示名（答辩表格用）
STRATEGY_LABELS: dict[str, str] = {
    "vector": "向量检索",
    "bm25": "BM25",
    "hybrid": "混合检索(RRF)",
    "hybrid_rerank": "混合+Rerank",
}


@dataclass
class StrategyResult:
    """单个策略在一个测试集上的评估结果。"""

    strategy: str
    items: list[ItemMetrics] = field(default_factory=list)
    seconds: float = 0.0
    error: str = ""
    #: 每个问题实际送入的条数（用于解释 rerank 策略的 Recall@k 为何相同）
    top_k_used: int = 0
    #: 判定粒度：chunk（按 chunk_id）或 page（按文件名+页码）
    judge_level: str = "chunk"
    #: 逐题检索到的 id 序列（仅 keep_retrieved=True 时填充）。精排是"全量打分 → 排序
    #: → 截断"，排序结果与 k 无关，故小档位结果必是大档位结果的前缀，可只精排一次。
    retrieved_ids: list[list[str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.items)

    def overall(self, recall_ks: Sequence[int] | None = None) -> dict[str, Any]:
        ks = recall_ks or (1, 3, 5, 10, 20)
        data = aggregate(self.items, ks)
        data["strategy"] = self.strategy
        data["label"] = STRATEGY_LABELS.get(self.strategy, self.strategy)
        data["seconds"] = round(self.seconds, 2)
        data["judge_level"] = self.judge_level
        if self.error:
            data["error"] = self.error
        return data

    def by_category(self, recall_ks: Sequence[int] | None = None) -> dict[str, dict[str, Any]]:
        """按类别（术语精确/语义泛化/多跳推理）分别聚合。"""
        ks = recall_ks or (1, 3, 5, 10, 20)
        grouped: dict[str, list[ItemMetrics]] = {}
        for item in self.items:
            grouped.setdefault(item.category or "未分类", []).append(item)
        return {cat: aggregate(ms, ks) for cat, ms in grouped.items()}


@dataclass
class RetrievalEvalResult:
    """一次完整评估（含多个策略）的结果。"""

    results: dict[str, StrategyResult] = field(default_factory=dict)
    testset_path: str = ""
    chunk_strategy: str = ""
    recall_ks: list[int] = field(default_factory=lambda: list(settings.evaluation.recall_at_k))
    #: 判定粒度：chunk（按 chunk_id）或 page（按文件名+页码）
    judge_level: str = "chunk"
    started_at: str = ""
    finished_at: str = ""

    @property
    def ok_results(self) -> dict[str, StrategyResult]:
        return {k: v for k, v in self.results.items() if v.ok}

    def summary_frame(self) -> "Any":
        """主对比表：每行一个策略，列为各 Recall@k 档 + MRR / nDCG / 首命中名次 / 耗时。"""
        import pandas as pd

        rows = [r.overall(self.recall_ks) for r in self.results.values()]
        if not rows:
            return pd.DataFrame()
        cols = ["strategy", "label", "n"] + [
            f"recall@{k}" for k in self.recall_ks
        ] + ["mrr", "ndcg", "avg_first_hit_rank", "avg_seconds"]
        df = pd.DataFrame(rows)
        for c in cols:
            if c not in df.columns:
                df[c] = None
        return df[cols]

    def category_frame(self) -> "Any":
        """分维度表：每行「策略 × 类别」。"""
        import pandas as pd

        rows: list[dict[str, Any]] = []
        for strategy, result in self.results.items():
            if not result.ok:
                continue
            for cat, agg in result.by_category(self.recall_ks).items():
                rows.append({
                    "strategy": strategy,
                    "label": STRATEGY_LABELS.get(strategy, strategy),
                    "category": cat,
                    **{k: v for k, v in agg.items() if k != "strategy"},
                })
        if not rows:
            return pd.DataFrame()
        cols = ["strategy", "label", "category", "n", f"recall@{self.recall_ks[0] if self.recall_ks else 5}",
                "recall@5", "recall@10", "mrr", "ndcg"]
        df = pd.DataFrame(rows)
        return df[[c for c in cols if c in df.columns]]

    def per_question_frame(self) -> "Any":
        """逐题明细表：人工核查"哪类题效果差"时用。"""
        import pandas as pd

        rows: list[dict[str, Any]] = []
        for strategy, result in self.results.items():
            for item in result.items:
                rows.append(item.to_row(strategy))
        return pd.DataFrame(rows)

    def markdown_table(self) -> str:
        """把主对比表渲染成 Markdown。"""
        df = self.summary_frame()
        if df.empty:
            return "（无结果）"
        try:
            return df.to_markdown(index=False)
        except Exception:  # noqa: BLE001 - 缺 tabulate 时退化为简单格式
            return df.to_string(index=False)



def _extract_ids_and_timing(response: Any) -> tuple[list[str], float, dict[str, float]]:
    """从 RetrievalResponse 里取出 chunk_id 顺序与耗时。"""
    ids = list(getattr(response, "chunk_ids", []) or [])
    timing = dict(getattr(response, "timing", {}) or {})
    seconds = float(timing.get("total", 0.0))
    return ids, seconds, timing


def _page_keys_of_response(response: Any) -> list[str]:
    """
    把检索结果转成页码级判定键序列（形如 ``测试.pdf#p18``）。

    跨分块策略比较必须用页码粒度：切换分块策略后页码相同、chunk_id 完全不同，按
    chunk_id 比对会让 fixed 档全为 0（判定粒度错误，不是检索效果差）。同页多块保序
    去重为一页，否则会虚高 nDCG/Precision（IDCG 按唯一相关页数算）。
    """
    from src.evaluation.testset import page_key

    keys: list[str] = []
    seen: set[str] = set()
    for item in getattr(response, "results", []) or []:
        meta = dict(getattr(item, "metadata", {}) or {})
        src = str(getattr(item, "source", "") or meta.get("source", ""))
        page = meta.get("page", getattr(item, "page", ""))
        key = page_key(src, page)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def evaluate_strategy(
    testset: TestSet,
    strategy: str,
    *,
    chunk_strategy: str | None = None,
    top_k: int | None = None,
    show_progress: bool = True,
    limit: int = 0,
    judge_level: str = "chunk",
    keep_retrieved: bool = False,
) -> StrategyResult:
    """
    对单个策略跑完测试集。

    ``top_k`` 覆盖检索返回条数（rerank 策略下即精排输出条数）；``limit`` 只跑前 N 条
    （0 表示全部）。``judge_level``：``chunk`` 按 chunk_id 严格比对（要求检索与标注同一
    分块策略），``page`` 按「文件名+页码」比对 —— 分块策略消融必须用 page。
    ``keep_retrieved`` 保留逐题 id 序列，供 Top-k 前缀复用。
    :returns: StrategyResult
    """
    from src.retrieval.pipeline import get_pipeline

    if judge_level not in ("chunk", "page"):
        raise ValueError(f"judge_level 只能是 chunk 或 page，实际 {judge_level!r}")

    items = testset.items[:limit] if limit else testset.items
    if not items:
        return StrategyResult(strategy=strategy, error="测试集为空")

    pipeline = get_pipeline(chunk_strategy)
    ks = list(settings.evaluation.recall_at_k)
    result = StrategyResult(strategy=strategy, judge_level=judge_level)
    t_all = time.perf_counter()

    iterator: Iterable[TestItem] = items
    if show_progress:
        from tqdm import tqdm

        iterator = tqdm(items, desc=f"评估[{strategy}]", unit="问", ncols=92)

    for item in iterator:
        try:
            response = pipeline.retrieve(
                item.question, strategy=strategy, top_k=top_k, write_log=False,
            )
            ids, seconds, timing = _extract_ids_and_timing(response)
            result.top_k_used = len(ids)
            if judge_level == "page":
                retrieved = _page_keys_of_response(response)
                relevant = sorted(item.page_keys())
            else:
                retrieved = ids
                relevant = item.relevant_chunk_ids
            if keep_retrieved:
                result.retrieved_ids.append(list(retrieved))
            result.items.append(
                evaluate_item(
                    retrieved, relevant,
                    question=item.question, category=item.category,
                    recall_ks=ks,
                    mrr_k=settings.evaluation.mrr_at_k,
                    ndcg_k=settings.evaluation.ndcg_at_k,
                    seconds=seconds, timing=timing,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单题失败不应终止整轮评估
            logger.warning("[%s] 问题「%s」检索失败：%s", strategy, item.question[:24], exc)
            relevant = sorted(item.page_keys()) if judge_level == "page" else item.relevant_chunk_ids
            if keep_retrieved:
                result.retrieved_ids.append([])
            result.items.append(
                evaluate_item([], relevant,
                              question=item.question, category=item.category, recall_ks=ks)
            )

    result.seconds = time.perf_counter() - t_all
    logger.info(
        "策略 %s 评估完成（粒度=%s）: %d 题，用时 %.1fs，%s",
        strategy, judge_level, len(result.items), result.seconds, result.overall(ks),
    )
    return result


def evaluate_all(
    testset: TestSet | None = None,
    *,
    strategies: Sequence[str] = DEFAULT_STRATEGIES,
    chunk_strategy: str | None = None,
    top_k: int | None = None,
    limit: int = 0,
    show_progress: bool = True,
    judge_level: str = "chunk",
) -> RetrievalEvalResult:
    """
    对全部策略跑测试集，返回对比结果。

    ``testset`` 缺省时从配置文件加载；``limit`` 只跑前 N 条（0 表示全部）；
    ``judge_level`` 判定粒度见 ``evaluate_strategy``。
    """
    if testset is None:
        from src.evaluation.testset import load_testset

        testset = load_testset()

    if not testset.is_valid:
        raise ValueError(
            "测试集校验未通过，请先修正后再评估。\n"
            + "\n".join(str(x) for x in testset.errors[:10])
        )

    out = RetrievalEvalResult(
        testset_path=str(testset.path or ""),
        chunk_strategy=chunk_strategy or settings.chunk.default_strategy,
        recall_ks=list(settings.evaluation.recall_at_k),
        judge_level=judge_level,
        started_at=datetime.now().isoformat(timespec="seconds"),
    )
    for strategy in strategies:
        out.results[strategy] = evaluate_strategy(
            testset, strategy, chunk_strategy=chunk_strategy,
            top_k=top_k, show_progress=show_progress, limit=limit,
            judge_level=judge_level,
        )
    out.finished_at = datetime.now().isoformat(timespec="seconds")
    return out



def save_retrieval_results(
    result: RetrievalEvalResult,
    out_dir: str | Path | None = None,
    *,
    stem: str = "retrieval_comparison",
) -> dict[str, Path]:
    """
    导出评估结果到 ``data/eval/results/``：
    ``{stem}.csv`` 主对比表、``{stem}_by_category.csv`` 分维度表、
    ``{stem}_per_question.csv`` 逐题明细、``{stem}.md`` Markdown 表格。
    """
    directory = Path(out_dir) if out_dir else settings.evaluation.result_dir
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    main = result.summary_frame()
    if not main.empty:
        p = directory / f"{stem}.csv"
        # utf-8-sig：Excel 直接打开不乱码
        main.to_csv(p, index=False, encoding="utf-8-sig")
        written["main"] = p

        md = directory / f"{stem}.md"
        md.write_text(
            f"# 检索策略对比\n\n测试集：`{result.testset_path}`（{len(result.results)} 种策略，"
            f"分块策略 `{result.chunk_strategy}`）\n\n"
            f"评估时间：{result.started_at} ~ {result.finished_at}\n\n"
            + result.markdown_table() + "\n",
            encoding="utf-8",
        )
        written["markdown"] = md

    cat = result.category_frame()
    if not cat.empty:
        p = directory / f"{stem}_by_category.csv"
        cat.to_csv(p, index=False, encoding="utf-8-sig")
        written["by_category"] = p

    per_q = result.per_question_frame()
    if not per_q.empty and settings.evaluation.save_raw_records:
        p = directory / f"{stem}_per_question.csv"
        per_q.to_csv(p, index=False, encoding="utf-8-sig")
        written["per_question"] = p

    logger.info("评估结果已导出: %s", {k: str(v) for k, v in written.items()})
    return written
