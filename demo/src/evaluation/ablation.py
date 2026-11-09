"""消融实验，对比分块策略与精排保留条数。

分块策略消融在固定长度与递归语义两套索引上跑同一测试集，回答语义分块是否值得。精排保留条数消融固定混合后精排策略，只改精排输出条数：太小会漏掉依据，太大会引入噪声并增加延迟。分块策略消融需要两套索引都已建好，重建代价高且会覆盖现有索引，默认不做。

检索类指标不消耗大模型调用额度；精排比较慢，保留条数消融采用单次精排加前缀截断，跑三档只花一档时间。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from config import settings
from src.evaluation.metrics import evaluate_item
from src.evaluation.retrieval_eval import (
    RetrievalEvalResult,
    StrategyResult,
    evaluate_strategy,
)
from src.evaluation.testset import TestSet
from src.utils.logger import get_logger

logger = get_logger(__name__)



def available_chunk_strategies() -> list[str]:
    """检查磁盘上实际存在哪些分块策略的索引。"""
    from src.ingest.indexer import bm25_path, chunks_file

    ready: list[str] = []
    for strategy in settings.chunk.available_strategies:
        if chunks_file(strategy).exists() and bm25_path(strategy).exists():
            ready.append(strategy)
    return ready


def ablation_chunk_strategies(
    testset: TestSet,
    *,
    retrieval_strategy: str = "hybrid_rerank",
    chunk_strategies: Sequence[str] | None = None,
    limit: int = 0,
    top_k: int | None = None,
    show_progress: bool = True,
) -> RetrievalEvalResult:
    """
    分块策略消融，比较固定长度分块与递归语义分块。

    只在已建好索引的策略上评估，缺失的跳过并记录原因。检索策略参数指定用于比较的检索策略。

    必须使用页码粒度判定：同一页在不同分块策略下编号不同，而文件名加页码一致，按编号判定会让标注全部失效，指标全为零，属于结构性错误而非模型问题。
    """
    strategies = list(chunk_strategies or settings.evaluation.ablation_chunk_strategies)
    ready = available_chunk_strategies()
    out = RetrievalEvalResult(
        testset_path=str(testset.path or ""),
        chunk_strategy=f"对比 {strategies}",
        judge_level="page",
        started_at=datetime.now().isoformat(timespec="seconds"),
    )

    for strategy in strategies:
        if strategy not in ready:
            logger.warning("分块策略 %s 的索引不存在，跳过（可用 --rebuild 重建）", strategy)
            out.results[strategy] = StrategyResult(
                strategy=strategy,
                error=f"索引不存在（已有：{ready or '无'}）；请先运行 "
                      f"`python -m src.ingest.cli all --strategy {strategy}`",
            )
            continue
        # 用分块策略名作为结果键，便于直接生成对比表
        result = evaluate_strategy(
            testset, retrieval_strategy, chunk_strategy=strategy,
            top_k=top_k, show_progress=show_progress, limit=limit,
            judge_level="page",
        )
        result.strategy = f"{strategy}+{retrieval_strategy}"
        out.results[strategy] = result

    out.finished_at = datetime.now().isoformat(timespec="seconds")
    return out



@dataclass
class RerankTopKResult:
    """精排保留条数消融结果。"""

    # 每个保留条数对应的评估结果
    results: dict[int, StrategyResult] = field(default_factory=dict)
    retrieval_strategy: str = "hybrid_rerank"
    chunk_strategy: str = ""
    testset_path: str = ""
    started_at: str = ""
    finished_at: str = ""

    def summary_frame(self) -> "Any":
        """
        每行一个保留条数档位。

        准确率取该行自身条数下的值，即送入上下文的内容有多少真正相关；不使用固定的前五条准确率，因为保留三条的档位没有第四与第五条，硬取前五条会得到与保留三条相同的数值，反而误导。
        """
        import pandas as pd

        ks = list(settings.evaluation.recall_at_k)
        rows: list[dict[str, Any]] = []
        for top_k, result in sorted(self.results.items()):
            # 把自身条数并入聚合档位，否则取不到该条数下的准确率
            agg = result.overall(sorted(set(ks) | {int(top_k)}))
            rows.append({
                "top_k": top_k,
                "n": agg.get("n", 0),
                **{f"recall@{k}": agg.get(f"recall@{k}") for k in ks},
                "mrr": agg.get("mrr"),
                "ndcg": agg.get("ndcg"),
                "precision@k": agg.get(f"precision@{top_k}"),
                "avg_seconds": agg.get("avg_seconds"),
                "error": result.error,
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def markdown_table(self) -> str:
        df = self.summary_frame()
        if df.empty:
            return "（无结果）"
        try:
            return df.to_markdown(index=False)
        except Exception:  # noqa: BLE001
            return df.to_string(index=False)


def ablation_rerank_top_k(
    testset: TestSet,
    *,
    top_k_values: Sequence[int] | None = None,
    chunk_strategy: str | None = None,
    limit: int = 0,
    show_progress: bool = True,
) -> RerankTopKResult:
    """
    精排保留条数消融，比较精排后保留三条、五条与八条的效果。

    固定混合后精排策略：保留条数只在精排之后才有意义，纯混合检索的条数是融合候选池大小，语义不同。召回率随保留条数增大而提高，但准确率下降、耗时上升，要选召回够用且噪声可接受的拐点。

    单次精排、多次截断：精排先对全部候选打分再排序截断，与条数无关，因此较小条数结果一定是最大条数结果的前缀。故只用最大条数跑一遍，再对每个条数取前缀重算指标，与逐档独立执行等价，耗时从多次精排降为一次。
    """
    values = sorted({int(v) for v in (top_k_values or settings.evaluation.ablation_rerank_top_k)})
    if not values:
        values = [settings.rerank.top_k]
    out = RerankTopKResult(
        chunk_strategy=chunk_strategy or settings.chunk.default_strategy,
        testset_path=str(testset.path or ""),
        started_at=datetime.now().isoformat(timespec="seconds"),
    )

    # 一次性跑最大档位，保留逐题完整精排序列表
    max_k = max(values)
    logger.info("Rerank Top-k 消融：单次精排取 Top-%d，再前缀截断出 %s…", max_k, values)
    base = evaluate_strategy(
        testset, "hybrid_rerank", chunk_strategy=chunk_strategy,
        top_k=max_k, show_progress=show_progress, limit=limit,
        keep_retrieved=True,
    )

    items = testset.items[:limit] if limit else testset.items
    ks = list(settings.evaluation.recall_at_k)
    for k in values:
        # 对每个条数取前缀重算指标，与单独执行该条数等价；需把条数本身并入统计档位，否则不在常规档位中时取不到该条数的准确率
        row_ks = sorted(set(ks) | {k})
        derived = StrategyResult(
            strategy=base.strategy,
            seconds=base.seconds,
            error=base.error,
            top_k_used=min(k, base.top_k_used),
            judge_level=base.judge_level,
        )
        for item, metrics, ids in zip(items, base.items, base.retrieved_ids):
            relevant = item.relevant_chunk_ids
            derived.items.append(
                evaluate_item(
                    list(ids)[:k], relevant,
                    question=item.question, category=item.category,
                    recall_ks=row_ks,
                    mrr_k=settings.evaluation.mrr_at_k,
                    ndcg_k=settings.evaluation.ndcg_at_k,
                    seconds=metrics.seconds, timing=metrics.timing,
                )
            )
        out.results[k] = derived

    out.finished_at = datetime.now().isoformat(timespec="seconds")
    return out


def save_ablation_results(
    chunk_result: RetrievalEvalResult | None = None,
    rerank_result: RerankTopKResult | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Path]:
    """导出消融结果，包含表格文件与说明文档。"""
    directory = Path(out_dir) if out_dir else settings.evaluation.result_dir
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    if chunk_result is not None:
        df = chunk_result.summary_frame()
        if not df.empty:
            p = directory / "ablation_chunk_strategy.csv"
            df.to_csv(p, index=False, encoding="utf-8-sig")
            written["chunk_csv"] = p
            md = directory / "ablation_chunk_strategy.md"
            md.write_text(
                "# 分块策略消融（固定长度 vs 递归语义）\n\n"
                f"测试集：`{chunk_result.testset_path}`\n\n"
                f"检索策略：{chunk_result.chunk_strategy}\n\n"
                f"{chunk_result.markdown_table()}\n",
                encoding="utf-8",
            )
            written["chunk_md"] = md

    if rerank_result is not None:
        df = rerank_result.summary_frame()
        if not df.empty:
            p = directory / "ablation_rerank_topk.csv"
            df.to_csv(p, index=False, encoding="utf-8-sig")
            written["rerank_csv"] = p
            md = directory / "ablation_rerank_topk.md"
            md.write_text(
                "# Rerank Top-k 消融\n\n"
                f"测试集：`{rerank_result.testset_path}`\n\n"
                f"分块策略：{rerank_result.chunk_strategy}\n\n"
                f"{rerank_result.markdown_table()}\n",
                encoding="utf-8",
            )
            written["rerank_md"] = md

    logger.info("消融结果已导出: %s", {k: str(v) for k, v in written.items()})
    return written
