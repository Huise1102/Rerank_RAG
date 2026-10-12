"""生成质量评估：每种策略跑「检索 → 生成 → LLM 裁判打分」，输出平均分。

每条样本消耗两次 LLM 调用（生成约 4000 + 裁判约 1500 tokens），10 条题 × 4 策略 ≈ 80 次
调用。检索类指标不消耗 token 可放心全量跑，生成类务必先小样本验证：``--limit N`` 只跑前
N 条，``config.evaluation.generation_sample`` 是配置级抽样上限。

维度：``faithfulness``（是否编造）、``answer_relevancy``（是否切题），两者独立。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from config import settings
from src.evaluation.judge import JudgeScore, LLMJudge, aggregate_scores
from src.evaluation.retrieval_eval import DEFAULT_STRATEGIES, STRATEGY_LABELS
from src.evaluation.testset import TestItem, TestSet
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class GenerationEvalResult:
    """生成质量评估结果。"""

    #: 策略 -> 该策略下所有裁判结果
    scores: dict[str, list[JudgeScore]] = field(default_factory=dict)
    #: 策略 -> 逐条明细（问题、答案、引用、耗时），便于人工复核
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    testset_path: str = ""
    chunk_strategy: str = ""
    judge_model: str = ""
    limit: int = 0
    started_at: str = ""
    finished_at: str = ""
    #: 生成阶段累计 token（按策略）
    tokens: dict[str, int] = field(default_factory=dict)

    def summary_frame(self) -> "Any":
        """主对比表：每行一个策略，列为各维度平均分。"""
        import pandas as pd

        rows: list[dict[str, Any]] = []
        for strategy, scores in self.scores.items():
            agg = aggregate_scores(scores)
            row = {
                "strategy": strategy,
                "label": STRATEGY_LABELS.get(strategy, strategy),
                **agg,
                "gen_tokens": self.tokens.get(strategy, 0),
            }
            rows.append(row)
        if not rows:
            return pd.DataFrame()
        metrics = list(settings.evaluation.judge_metrics)
        cols = ["strategy", "label", "n", "n_scored"] + metrics + ["errors", "avg_seconds", "gen_tokens"]
        df = pd.DataFrame(rows)
        return df[[c for c in cols if c in df.columns]]

    def category_frame(self) -> "Any":
        """分维度表：策略 × 类别 × 平均分。"""
        import pandas as pd

        metrics = list(settings.evaluation.judge_metrics)
        rows: list[dict[str, Any]] = []
        for strategy, scores in self.scores.items():
            grouped: dict[str, list[JudgeScore]] = {}
            for s in scores:
                grouped.setdefault(s.category or "未分类", []).append(s)
            for cat, group in grouped.items():
                agg = aggregate_scores(group)
                rows.append({
                    "strategy": strategy,
                    "label": STRATEGY_LABELS.get(strategy, strategy),
                    "category": cat,
                    **{m: agg.get(m) for m in metrics},
                    "n": agg["n"],
                })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        cols = ["strategy", "label", "category", "n"] + metrics
        return df[[c for c in cols if c in df.columns]]

    def per_question_frame(self) -> "Any":
        """
        逐题明细：含答案与裁判理由，人工复核用。

        另附两个可程序化发现幻觉的字段，不依赖裁判的主观描述：

        - ``n_retrieved`` / ``n_contexts``：检索条数 vs 真正进 prompt 的条数，两者不等
          说明被上下文预算裁剪过，引用编号的有效范围以 ``n_contexts`` 为准。
        - ``invalid_citations``：模型引用了但不存在的编号个数（如只有 5 条上下文却写 `[9]`）。
        """
        import pandas as pd

        rows: list[dict[str, Any]] = []
        for strategy, scores in self.scores.items():
            records = {r["question"]: r for r in self.records.get(strategy, [])}
            for s in scores:
                rec = records.get(s.question, {})
                rows.append({
                    **s.to_row(),
                    "answer_chars": len(rec.get("answer", "")),
                    "n_retrieved": rec.get("n_retrieved", 0),
                    "n_contexts": rec.get("n_contexts", 0),
                    "citations": rec.get("citations", 0),
                    "invalid_citations": len(rec.get("invalid_indices", []) or []),
                    "is_refusal": rec.get("is_refusal", False),
                    "gen_seconds": rec.get("gen_seconds", 0.0),
                })
        return pd.DataFrame(rows)

    def markdown_table(self) -> str:
        df = self.summary_frame()
        if df.empty:
            return "（无结果）"
        try:
            return df.to_markdown(index=False)
        except Exception:  # noqa: BLE001
            return df.to_string(index=False)



def _stratified_sample(items: Sequence[TestItem], n: int) -> list[TestItem]:
    """
    分层抽样：按类别轮流取，保证小样本也覆盖三个类别。

    测试集按「语义泛化 → 术语精确 → 多跳推理」顺序排列，直接取前 N 条会全是语义泛化类；
    而生成评估每条约 5500 token，小样本是常态，只覆盖一类的得分容易被误读成整体生成质量。

    抽样是确定性的（按类别轮流 + 原顺序），同输入必得同样本，保证多策略评同一批题。
    """
    if n <= 0 or n >= len(items):
        return list(items)
    buckets: dict[str, list[TestItem]] = {}
    for it in items:
        buckets.setdefault(it.category or "未分类", []).append(it)
    # 类别顺序按首次出现固定，避免 dict 顺序在不同 Python 版本下漂移
    order = list(buckets)
    picked: list[TestItem] = []
    idx = 0
    while len(picked) < n:
        progressed = False
        for cat in order:
            if idx < len(buckets[cat]):
                picked.append(buckets[cat][idx])
                progressed = True
                if len(picked) >= n:
                    break
        if not progressed:
            break
        idx += 1
    return picked


def evaluate_generation(
    testset: TestSet | None = None,
    *,
    strategies: Sequence[str] = DEFAULT_STRATEGIES,
    chunk_strategy: str | None = None,
    limit: int = 0,
    judge: LLMJudge | None = None,
    llm_client: Any = None,
    show_progress: bool = True,
) -> GenerationEvalResult:
    """
    对每种策略跑「检索 → 生成 → 裁判打分」。

    ``limit`` 为抽样条数（0 表示全部，或取配置的 ``generation_sample``），抽样按类别
    分层进行，避免"前 N 条全是语义泛化类"。``judge`` 与 ``llm_client`` 可注入 mock，
    测试时零 token。
    """
    from src.generation.llm import LLMClient, get_llm_client
    from src.retrieval.pipeline import get_pipeline

    if testset is None:
        from src.evaluation.testset import load_testset

        testset = load_testset()
    if not testset.is_valid:
        raise ValueError(
            "测试集校验未通过，请先修正后再评估。\n"
            + "\n".join(str(x) for x in testset.errors[:10])
        )

    effective_limit = limit or settings.evaluation.generation_sample
    items: list[TestItem] = (
        _stratified_sample(testset.items, effective_limit)
        if effective_limit else list(testset.items)
    )
    if effective_limit and len(items) < len(testset.items):
        dist: dict[str, int] = {}
        for it in items:
            dist[it.category] = dist.get(it.category, 0) + 1
        logger.info("生成评估抽样（分层）：%s",
                    " / ".join(f"{k} {v}" for k, v in dist.items()))

    judge = judge or LLMJudge()
    generator = llm_client or get_llm_client()
    pipeline = get_pipeline(chunk_strategy)

    out = GenerationEvalResult(
        testset_path=str(testset.path or ""),
        chunk_strategy=chunk_strategy or settings.chunk.default_strategy,
        judge_model=judge.model,
        limit=effective_limit,
        started_at=datetime.now().isoformat(timespec="seconds"),
    )

    logger.info(
        "开始生成质量评估：%d 条题 × %d 种策略 = %d 次生成 + 同量裁判调用（judge=%s）",
        len(items), len(strategies), len(items) * len(strategies), judge.model,
    )

    for strategy in strategies:
        scores: list[JudgeScore] = []
        records: list[dict[str, Any]] = []
        tokens = 0

        iterator: Any = items
        if show_progress:
            from tqdm import tqdm

            iterator = tqdm(items, desc=f"生成评估[{strategy}]", unit="问", ncols=92)

        for item in iterator:
            try:
                t0 = time.perf_counter()
                retrieval = pipeline.retrieve(
                    item.question, strategy=strategy, write_log=False,
                )
                response = generator.generate(item.question, retrieval.results)
                gen_seconds = time.perf_counter() - t0
                tokens += int((response.usage or {}).get("total_tokens", 0) or 0)

                # 裁判必须拿到生成阶段**实际使用**的上下文，而不是检索到的全部结果：
                # 生成会按 context_max_chars 预算裁剪（本项目 6000 字，例如 vector 检索
                # 20 条只用 19 条进 prompt），编号体系不一致会把模型的正常引用误判成
                # "引用了不存在的编号"（假阳性幻觉）。
                judge_contexts = list(response.contexts) or list(retrieval.results)
                score = judge.score(
                    item.question, judge_contexts, response.answer,
                    strategy=strategy, category=item.category,
                )
            except Exception as exc:  # noqa: BLE001 - 单题失败继续跑后面的
                logger.warning("[%s] 问题「%s」评估失败：%s", strategy, item.question[:24], exc)
                score = JudgeScore(question=item.question, strategy=strategy,
                                   category=item.category, error=f"{type(exc).__name__}: {exc}")
                response = None
                retrieval = None
                gen_seconds = 0.0

            scores.append(score)
            report = response.citations if response is not None else None
            records.append({
                "question": item.question,
                "category": item.category,
                "answer": response.answer if response is not None else "",
                # n_contexts = 真正进入 prompt 的条数（决定引用编号的有效范围）
                "n_contexts": len(response.contexts) if response is not None else 0,
                # n_retrieved = 检索返回条数（两者不等说明被上下文预算裁剪过）
                "n_retrieved": len(retrieval.results) if retrieval is not None else 0,
                "citations": len(report.citations) if report is not None else 0,
                "invalid_indices": list(getattr(report, "invalid_indices", []) or []) if report else [],
                "unused_contexts": list(getattr(report, "unused_contexts", []) or []) if report else [],
                "is_refusal": bool(report.is_refusal) if report is not None else False,
                "gen_seconds": round(gen_seconds, 2),
                "relevant_chunk_ids": item.relevant_chunk_ids,
            })

        out.scores[strategy] = scores
        out.records[strategy] = records
        out.tokens[strategy] = tokens
        logger.info("[%s] 生成评估完成：%s", strategy, aggregate_scores(scores))

    out.finished_at = datetime.now().isoformat(timespec="seconds")
    return out



def save_generation_results(
    result: GenerationEvalResult,
    out_dir: str | Path | None = None,
    *,
    stem: str = "generation_comparison",
) -> dict[str, Path]:
    """
    导出生成评估结果到 ``data/eval/results/``：
    ``{stem}.csv`` 策略 × 平均分、``{stem}_by_category.csv`` 策略 × 类别、
    ``{stem}_per_question.csv`` 逐题明细（含答案与裁判理由）、``{stem}.md``。
    """
    directory = Path(out_dir) if out_dir else settings.evaluation.result_dir
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    main = result.summary_frame()
    if not main.empty:
        p = directory / f"{stem}.csv"
        main.to_csv(p, index=False, encoding="utf-8-sig")
        written["main"] = p

        metrics = list(settings.evaluation.judge_metrics)
        metric_note = "、".join(f"{m}（1-{settings.evaluation.judge_scale} 分）" for m in metrics)
        md = directory / f"{stem}.md"
        md.write_text(
            f"# 生成质量评估（LLM-as-Judge）\n\n"
            f"测试集：`{result.testset_path}`"
            + (f"（抽样前 {result.limit} 条）" if result.limit else "（全量）")
            + f"\n\n裁判模型：`{result.judge_model}`（temperature="
            f"{settings.evaluation.judge_temperature}）\n\n"
            f"评分维度：{metric_note}\n\n"
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

    logger.info("生成评估结果已导出: %s", {k: str(v) for k, v in written.items()})
    return written
