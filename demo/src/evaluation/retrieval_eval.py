"""检索策略对比评估，对每种策略跑同一测试集，输出对比表与文件。

四种策略分别是以向量检索与 BM25 为基线、混合检索与混合后精排为完整链路。检索类指标不消耗大模型调用额度。混合后精排在中央处理器上每题约需二十秒，精排是瓶颈，正式评估前建议先用较小样本验证。
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

# 默认对比的策略
DEFAULT_STRATEGIES: tuple[str, ...] = ("vector", "bm25", "hybrid", "hybrid_rerank")

# 策略的中文显示名
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
    # 每个问题实际送入的条数，用于解释精排策略各截断位置召回率为何相同
    top_k_used: int = 0
    # 判定粒度，按文档块编号或按文件名加页码
    judge_level: str = "chunk"
    # 逐题检索到的编号序列，仅保留开关开启时填充。精排先对全部候选打分再排序截断，排序结果与条数无关，因此较小档位的结果是较大档位结果的前缀，精排只需执行一次
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
        """按类别分别聚合。"""
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
    # 判定粒度，按文档块编号或按文件名加页码
    judge_level: str = "chunk"
    started_at: str = ""
    finished_at: str = ""

    @property
    def ok_results(self) -> dict[str, StrategyResult]:
        return {k: v for k, v in self.results.items() if v.ok}

    def summary_frame(self) -> "Any":
        """主对比表，每行一个策略，列包含各截断位置的召回率、平均倒数排名、归一化折损累计增益、首个命中名次与耗时。"""
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
        """分维度表，每行对应一种策略与类别的组合。"""
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
        """把主对比表渲染为 Markdown 文本。"""
        df = self.summary_frame()
        if df.empty:
            return "（无结果）"
        try:
            return df.to_markdown(index=False)
        except Exception:  # noqa: BLE001 - 缺 tabulate 时退化为简单格式
            return df.to_string(index=False)



def _extract_ids_and_timing(response: Any) -> tuple[list[str], float, dict[str, float]]:
    """从检索响应中取出文档块编号顺序与各步耗时。"""
    ids = list(getattr(response, "chunk_ids", []) or [])
    timing = dict(getattr(response, "timing", {}) or {})
    seconds = float(timing.get("total", 0.0))
    return ids, seconds, timing


def _page_keys_of_response(response: Any) -> list[str]:
    """
    把检索结果转为页码级判定键序列，由文件名与页码拼接而成。

    跨分块策略比较必须使用页码粒度：切换策略后页码相同而编号完全不同，按编号比对会让固定长度策略的结果全为零。同一页的多个块按顺序去重为一页，否则会虚高归一化折损累计增益与准确率，因为理想排序按唯一相关页数计算。
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

    返回条数可显式覆盖，在混合后精排策略下即精排输出条数；样本上限为零表示全部。判定粒度可选按文档块编号严格比对，要求检索与标注使用同一分块策略，或按文件名加页码比对，分块策略消融必须使用页码粒度。保留检索结果开关，供截断位置的前缀复用。
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

    测试集缺省时从配置加载；样本上限为零表示全部，判定粒度含义见单策略评估。
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
    导出评估结果，包括主对比表、分维度表、逐题明细与 Markdown 表格，返回各文件路径。
    """
    directory = Path(out_dir) if out_dir else settings.evaluation.result_dir
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    main = result.summary_frame()
    if not main.empty:
        p = directory / f"{stem}.csv"
        # 使用带签名的 UTF-8 编码，Excel 直接打开不会乱码
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
