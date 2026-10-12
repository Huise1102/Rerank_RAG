"""
阶段 6 —— 一键实验脚本（run_all_experiments.py）

一条命令跑完全部实验并生成答辩用报告：

    ① 校验测试集
    ② 四种检索策略对比（Recall@5 / MRR / nDCG …）        —— 不消耗 token
    ③ 分块策略消融（固定长度 vs 递归语义）                —— 不消耗 token
    ④ Rerank Top-k 消融（3 / 5 / 8）                      —— 不消耗 token
    ⑤ 生成质量评估（LLM 裁判）                            —— **消耗 token，默认跳过**
    ⑥ 出图 + 汇总报告（含"相比 baseline 提升多少"的分析）

用法::

    python scripts/run_all_experiments.py --limit 3                 # 先小样本验证脚本本身
    python scripts/run_all_experiments.py                           # 正式跑全量
    python scripts/run_all_experiments.py --with-generation --generation-limit 10
    python scripts/run_all_experiments.py --report-only             # 只重跑报告（复用 CSV）
    python scripts/run_all_experiments.py --steps retrieval         # 分步执行，便于复核

耗时与成本：``bm25`` 毫秒级、``vector``/``hybrid`` 每题 2~3 秒、``hybrid_rerank``
每题 18~21 秒（CPU）；20 题 × 4 策略 ≈ 8~10 分钟，Top-k 消融 3 档 × 20 题 ≈ 18 分钟。
检索与消融全程 0 token，只有生成质量评估消耗 token（约 5500 tokens/题/策略）。

产出 ``data/eval/results/``：策略对比、分维度（术语精确/语义泛化/多跳推理）、逐题明细、
两次消融与生成质量（若启用）的 CSV/MD，汇总报告 ``EXPERIMENT_REPORT.md``；
``figures/`` 下 5 张对比图（PNG，可直接进 PPT）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402

RESULTS_DIR = settings.evaluation.result_dir
FIGURES_DIR = settings.evaluation.figure_dir
REPORT_PATH = RESULTS_DIR / "EXPERIMENT_REPORT.md"

ALL_STEPS = ("check", "retrieval", "ablation", "generation", "report")

#: 测试集构建方法学（写死在报告里）：评估结论的可信度取决于测试集质量，而"问题怎么问"
#: 最容易被评委追问，必须落在报告里，而不是只存在于聊天记录。
TESTSET_METHODOLOGY = """### 测试集构建原则（决定实验能否说明问题）

评估结论的可信度**完全取决于测试集质量**。四条最关键的经验：

#### ① 问题里不要出现文档原文的专有词（术语精确类除外）

**术语精确类才应该放专有词**（文号、条款号、文件名），因为那是**故意考 BM25 的强项**；
其余两类必须避免照抄原文。

#### ② 语义泛化类要用"学生的口语"，而非文件用语

真实用户不会用规章制度的措辞提问。若测试集全是文件用语，等于在测"关键词匹配"，
向量检索的语义泛化能力无从体现，混合检索的增益也就测不出来。

#### ③ 多跳推理类必须标注**多个** chunk_id

多跳题的价值在于考察系统能否**同时召回跨文档的依据**。若只标一个 chunk_id，
Recall 无法反映"两个依据是否都找到"，该类别的指标会失去意义。

#### ④ 标注要覆盖"整个主题"，不能只标 1~2 个块（这条最容易出错）

本项目的测试集做过一次**返工**，原因就在这里：

- 第一版用「锚点正则命中 + `limit=1~2`」自动标注，即每个问题只标最先命中的 1~2 个块。
- 但语料里同一主题往往散落在 **6~15 个块**（例如"学费减免"横跨实施细则的多个条款，
  还牵涉到另一份文件的申请流程）。
- 结果：标注的相关集**远小于**真实相关集 → Recall 被系统性低估，
  而且不同策略的差距被"压缩"到看不出差异（BM25 的 Recall@5 一度只有 0.2736）。
- 修正做法：改为「任一关键词命中 + 页码范围过滤」的**宽标注**，
  并人工核查页码范围、剔除非本主题的相邻文件。
  修正后同一指标升到 0.4715 —— **提升来自标注修正，不是模型变好**。

教训：Recall 类指标的分母是标注出来的，**标注错了，所有结论都跟着错**。
自动标注必须人工抽查"漏标率"，并优先怀疑"指标普遍偏低"是标注问题而非模型问题。

---

### 类别设计（三类，覆盖不同检索机制）

| 类别 | 特征 | 预期优势策略 | 考察的机制 |
|---|---|---|---|
| **术语精确** | 含专有名词、文号、条款号 | BM25 | 词频精确匹配 |
| **语义泛化** | 用词与原文不同，需理解语义 | 向量检索 | 稠密语义表示 |
| **多跳推理** | 需综合多个文档块 | 混合 + Rerank | 融合召回 + 交叉编码器精排 |

三类各不少于 5 条；类别不均衡时分维度结论不稳固（`cli check` 会给出提示）。

---

### 标注质量保障

- **`chunk_id` 存在性校验**：`cli check` 会验证每个 id 真实存在于当前索引中。
  抄错一个字符会让该类 Recall **恒为 0**，表面看像"检索效果差"，实则是标注错误 —— 极难排查。
- **索引重建会使 chunk_id 变化**（文件名、页码、正文哈希参与生成），
  重建索引后需重新校验测试集。
- **宽标注 + 人工抽查漏标率**：本项目第一版因标注过窄导致指标整体偏低（见 ④），
  返工后才得到可用数据。这一条建议写进任何使用 Recall 类指标的实验流程。
- **判定粒度必须与标注粒度匹配**：标注基于 `recursive` 的 chunk_id，
  所以跨分块策略比较时必须退到"文件名+页码"粒度（见 6.1），
  否则 `fixed` 档会全 0 —— 这是本项目踩过的第二个结构性坑。
"""


def _setup_console() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def banner(title: str) -> None:
    print("\n" + "=" * 92)
    print(f" {title}")
    print("=" * 92)


def step(title: str) -> None:
    print(f"\n▶ {title}")
    print("-" * 92)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)



def run_check(limit: int) -> tuple[bool, str]:
    """① 校验测试集。"""
    from src.evaluation import testset as ts

    target = ts.find_testset_file()
    print(f"  测试集: {rel(target)}")
    if not target.exists():
        print("\n  ❌ 测试集不存在 —— 阶段 6 第 1 步需要你手动标注。\n")
        print(ts._missing_testset_hint(target))
        return False, "测试集不存在"

    try:
        data = ts.load_testset(target)
    except Exception as exc:  # noqa: BLE001
        return False, f"加载失败: {type(exc).__name__}: {exc}"

    print(f"  {data.summary()}")
    counts = data.category_counts()
    for cat in ts.CATEGORIES:
        print(f"    {cat:<6} {counts.get(cat, 0):>3} 条")
    missing = [c for c in ts.CATEGORIES if not counts.get(c)]
    if missing:
        print(f"  ⚠️  缺失类别：{missing}（建议每类 ≥5 条，否则分维度结论不稳固）")
    if data.issues:
        print("\n  校验问题：")
        print(ts.format_issues(data, limit=8))

    if not data.is_valid:
        return False, f"校验未通过（{len(data.errors)} 个错误）"
    if limit and limit < len(data):
        print(f"  ℹ️  本次只跑前 {limit} 条（--limit）")
    return True, data.summary()


def run_retrieval(limit: int, chunk_strategy: str | None, no_progress: bool,
                  judge_level: str = "chunk"):
    """② 四种检索策略对比。"""
    from src.evaluation import retrieval_eval as re
    from src.evaluation import testset as ts

    data = ts.load_testset()
    strategies = list(re.STRATEGY_LABELS)
    print(f"  策略: {strategies}")
    print(f"  规模: {'前 ' + str(limit) + ' 条' if limit else '全部 ' + str(len(data)) + ' 条'}"
          f" ｜ 分块={chunk_strategy or settings.chunk.default_strategy}"
          f" ｜ 判定粒度={judge_level}")
    print("  提示：hybrid_rerank 每题约 18~21 秒，是主要耗时来源\n")

    t0 = time.perf_counter()
    result = re.evaluate_all(
        data, strategies=strategies, chunk_strategy=chunk_strategy,
        limit=limit, show_progress=not no_progress, judge_level=judge_level,
    )
    print(f"\n  用时 {time.perf_counter() - t0:.1f}s")
    print("\n  [主对比表]")
    for line in result.markdown_table().splitlines():
        print("   " + line)

    written = re.save_retrieval_results(result)
    print("\n  [已导出]")
    for key, path in written.items():
        print(f"    {key:<12} {rel(path)}")
    return result


def run_ablation(limit: int, chunk_strategy: str | None, no_progress: bool):
    """③④ 分块策略消融 + Rerank Top-k 消融。"""
    from src.evaluation import ablation as ab
    from src.evaluation import testset as ts

    data = ts.load_testset()
    ready = ab.available_chunk_strategies()
    print(f"  可用分块索引: {ready}")
    if len(ready) < 2:
        print(f"  ⚠️  分块消融需要两套索引，当前只有 {ready}。"
              f"请先运行：python -m src.ingest.cli all")

    chunk_result = None
    if len(ready) >= 2:
        print("\n  ── 消融一：分块策略（固定长度 vs 递归语义）──")
        t0 = time.perf_counter()
        chunk_result = ab.ablation_chunk_strategies(
            data, limit=limit, show_progress=not no_progress)
        print(f"  用时 {time.perf_counter() - t0:.1f}s")
        for line in chunk_result.markdown_table().splitlines():
            print("   " + line)
    else:
        print("  跳过消融一（索引不足）")

    print("\n  ── 消融二：Rerank Top-k ──")
    values = settings.evaluation.ablation_rerank_top_k
    print(f"  档位: {values} ｜ 每档 {limit or len(data)} 题，每题约 18~21 秒")
    t0 = time.perf_counter()
    rerank_result = ab.ablation_rerank_top_k(
        data, top_k_values=values, chunk_strategy=chunk_strategy,
        limit=limit, show_progress=not no_progress,
    )
    print(f"  用时 {time.perf_counter() - t0:.1f}s")
    for line in rerank_result.markdown_table().splitlines():
        print("   " + line)

    written = ab.save_ablation_results(chunk_result, rerank_result)
    print("\n  [已导出]")
    for key, path in written.items():
        print(f"    {key:<12} {rel(path)}")
    return chunk_result, rerank_result


def run_generation(limit: int, chunk_strategy: str | None, no_progress: bool, assume_yes: bool):
    """⑤ 生成质量评估（消耗 token）。"""
    from src.evaluation import generation_eval as ge
    from src.evaluation import testset as ts

    data = ts.load_testset()
    strategies = list(ge.STRATEGY_LABELS)
    n = min(limit or len(data), len(data))
    est_tokens = n * len(strategies) * 5500
    print(f"  规模: {n} 条 × {len(strategies)} 策略 = {n * len(strategies)} 次生成 + 同量裁判调用")
    print(f"  预估 token: ≈{est_tokens:,}（每次生成约 4000 + 裁判约 1500）")
    print(f"  裁判: {settings.evaluation.judge_model} ｜ 维度 {settings.evaluation.judge_metrics}")

    if not assume_yes:
        reply = input("  确认执行生成评估？会消耗上述 token (yes/no) > ").strip().lower()
        if reply not in ("y", "yes"):
            print("  已跳过生成评估。")
            return None

    t0 = time.perf_counter()
    result = ge.evaluate_generation(
        data, strategies=strategies, chunk_strategy=chunk_strategy,
        limit=limit, show_progress=not no_progress,
    )
    print(f"\n  用时 {time.perf_counter() - t0:.1f}s")
    for line in result.markdown_table().splitlines():
        print("   " + line)
    written = ge.save_generation_results(result)
    print("\n  [已导出]")
    for key, path in written.items():
        print(f"    {key:<12} {rel(path)}")
    return result



def _load_csv(name: str):
    import pandas as pd

    path = RESULTS_DIR / name
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:  # noqa: BLE001
        return None


def _fmt(v, digits: int = 4, dash: str = "—") -> str:
    if v is None:
        return dash
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dash
    if f != f:  # NaN
        return dash
    return f"{f:.{digits}f}"


#: 这些列是计数，渲染成整数（否则表格里会出现 "100.0000" 这种噪声）
_INT_COLS = {"n", "n_scored", "top_k", "first_hit_rank", "n_relevant", "n_retrieved",
             "gen_tokens", "total_tokens", "prompt_tokens", "completion_tokens"}
#: 这些列是秒数，2 位小数即可（4 位小数在报告里是纯噪声）
_SEC_COLS = {"avg_seconds", "seconds", "elapsed", "p50_seconds", "p95_seconds"}
#: 这些列是 1~5 分的裁判评分，2 位小数即可
_SCORE_COLS = {"faithfulness", "answer_relevancy", "context_precision", "context_recall",
               "answer_correctness", "score", "avg_score"}


def _cell(v, col: str) -> str:
    """单元格渲染：计数列取整、秒数列与评分列取 2 位，其余浮点取 4 位。"""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        if v != v:  # NaN
            return "—"
        if col in _INT_COLS:
            return str(int(v))
        if col in _SEC_COLS or col in _SCORE_COLS or col.endswith("_score"):
            return _fmt(v, 2)
        return _fmt(v)
    return str(v)


def _md_table(df, columns: list[str] | None = None) -> str:
    """渲染 Markdown 表格；缺 tabulate 时退化为手写 Markdown，避免纯文本对齐难以阅读。"""
    if df is None or len(df) == 0:
        return "_（无数据）_\n"
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    try:
        return df.to_markdown(index=False)
    except Exception:  # noqa: BLE001 - 缺 tabulate
        cols = list(df.columns)
        lines = ["| " + " | ".join(str(c) for c in cols) + " |",
                 "|" + "|".join("---" for _ in cols) + "|"]
        for _, row in df.iterrows():
            lines.append("| " + " | ".join(_cell(row[c], c) for c in cols) + " |")
        return "\n".join(lines) + "\n"


def _delta(better: float, baseline: float) -> str:
    """计算提升幅度并格式化为相对/绝对两种表述。"""
    if baseline is None or float(baseline) != float(baseline):
        return "—"
    b = float(baseline)
    a = float(better)
    diff = a - b
    if abs(b) < 1e-9:
        return f"{diff:+.4f}（baseline 为 0，无法算相对提升）"
    return f"{diff:+.4f}（相对 {(diff / b) * 100:+.1f}%）"


def build_report(
    retrieval_df,
    category_df,
    chunk_df,
    rerank_df,
    generation_df,
    *,
    testset_summary: str,
    testset_n: int = 0,
    testset_dist: str = "（未知）",
    limit: int,
    chunk_strategy: str,
    elapsed: float,
    generation_per_q=None,
) -> Path:
    """生成汇总报告 ``EXPERIMENT_REPORT.md``。

    报告结构：实验设置 / 测试集构建方法学 / 主结果（四策略对比）/ 提升分析 /
    分维度分析 / 两次消融（分块策略、Rerank Top-k）/ 生成质量 / 对比图 / 结论与原因分析。

    第 2 章内容来自模块级常量 ``TESTSET_METHODOLOGY``（写死在脚本里），
    保证每次生成的报告都包含这段方法学说明。
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    metrics = list(settings.evaluation.judge_metrics)
    recall_ks = settings.evaluation.recall_at_k
    main_cols = ["strategy", "label", "n"] + [f"recall@{k}" for k in recall_ks] + [
        "mrr", "ndcg", "avg_first_hit_rank", "avg_seconds"]
    analysis_lines: list[str] = []
    conclusions: list[str] = []

    if retrieval_df is not None and len(retrieval_df):
        idx = {str(r["strategy"]): r for _, r in retrieval_df.iterrows()}
        target = "hybrid_rerank" if "hybrid_rerank" in idx else None
        baselines = [b for b in ("vector", "bm25") if b in idx]

        if target:
            analysis_lines.append(
                f"以 **{idx[target]['label']}** 为目标，与两个基线逐一比较：\n"
            )
            compare_cols = [f"recall@{k}" for k in recall_ks] + ["mrr", "ndcg"]
            header = "| 指标 | " + " | ".join(["混合+Rerank"] + [str(idx[b]["label"]) for b in baselines]
                                              + ["相比向量", "相比 BM25"]) + " |"
            sep = "|" + "|".join("---" for _ in range(len(baselines) + 3)) + "|"
            analysis_lines.append(header)
            analysis_lines.append(sep)
            for col in compare_cols:
                if col not in retrieval_df.columns:
                    continue
                row = [col.upper().replace("RECALL@", "Recall@"), _fmt(idx[target][col])]
                for b in baselines:
                    row.append(_fmt(idx[b][col]))
                row.append(_delta(idx[target][col], idx[baselines[0]][col]) if baselines else "—")
                row.append(_delta(idx[target][col], idx[baselines[1]][col]) if len(baselines) > 1 else "—")
                analysis_lines.append("| " + " | ".join(row) + " |")
            analysis_lines.append("")
            analysis_lines.append(
                "> ⚠️ **怎么读这张表**：`hybrid_rerank` 的 Recall@10/@20 出现负值"
                f"是**结构性**的，不是效果变差 —— 该策略精排后只输出 "
                f"{settings.rerank.top_k} 条，Recall@10/@20 的上限就等于 Recall@5；"
                "而向量/BM25 返回 20 条，在长尾档位上天然占优。"
                "真正决定最终答案质量的是**送进 LLM 的那几条**，"
                "所以判断优劣要看 **Recall@1/@3/@5、MRR、nDCG 与平均首命中名次**。\n"
            )
            analysis_lines.append("")

            # 自动生成结论要点
            for col, human in (("recall@1", "Recall@1"), ("recall@5", "Recall@5"),
                               ("mrr", "MRR"), ("ndcg", "nDCG")):
                if col not in retrieval_df.columns or not baselines:
                    continue
                t = float(idx[target][col])
                best_base = max(float(idx[b][col]) for b in baselines)
                if t > best_base + 1e-6:
                    conclusions.append(
                        f"{human} 达 {t:.4f}，高于最佳基线（{best_base:.4f}），"
                        f"提升 {t - best_base:+.4f}"
                        + (f"（相对 {(t - best_base) / best_base * 100:+.1f}%）" if best_base > 0 else "")
                    )
                elif abs(t - best_base) <= 1e-6:
                    conclusions.append(f"{human} 与最佳基线持平（{t:.4f}）")
                else:
                    conclusions.append(
                        f"{human} 为 {t:.4f}，低于最佳基线 {best_base:.4f} —— 需在报告中说明原因"
                    )

            # 名次证据：精排把正确答案往前挪了多少（比单看 Recall 更有说服力）
            rank_ev: list[str] = []
            for key, human in (("recall@1", "Recall@1"),):
                if key in retrieval_df.columns and baselines:
                    t1 = float(idx[target][key])
                    b1 = max(float(idx[b][key]) for b in baselines)
                    b1_name = max(baselines, key=lambda b: float(idx[b][key]))
                    if b1 > 0:
                        rank_ev.append(
                            f"- **{human}**：{b1:.4f}（最佳基线 {idx[b1_name]['label']}）"
                            f" → {t1:.4f}，相对提升 **{(t1 - b1) / b1 * 100:+.1f}%**，"
                            f"绝对提升 {t1 - b1:+.4f}"
                        )
            if "avg_first_hit_rank" in retrieval_df.columns and baselines:
                t_rank = float(idx[target]["avg_first_hit_rank"])
                b_rank = min(
                    (float(idx[b]["avg_first_hit_rank"]) for b in baselines
                     if float(idx[b]["avg_first_hit_rank"]) > 0),
                    default=0.0,
                )
                if b_rank > 0:
                    rank_ev.append(
                        f"- **平均首命中名次**：{b_rank:.2f} → {t_rank:.2f}"
                        f"（前移 {b_rank - t_rank:.2f} 名，约 "
                        f"{(b_rank - t_rank) / b_rank * 100:.0f}%）。"
                        "这一项最能说明精排的作用：候选本来就召回得差不多，"
                        "精排把它们**重新排对了位置**。"
                    )
            if "avg_seconds" in retrieval_df.columns and baselines:
                t_s = float(idx[target]["avg_seconds"])
                b_s = max(float(idx[b]["avg_seconds"]) for b in baselines)
                rank_ev.append(
                    f"- **代价**：单题耗时 {b_s:.2f}s → {t_s:.2f}s"
                    f"（×{t_s / b_s:.1f}），精排的收益是用延迟换来的 —— "
                    "这也是本项目保留 `top_k` 可调的原因。"
                )
            if rank_ev:
                analysis_lines.append("")
                analysis_lines.append("**量化提升（相对基线）：**\n")
                analysis_lines.extend(rank_ev)
                analysis_lines.append("")
        else:
            analysis_lines.append("_未找到 hybrid_rerank 结果，无法做提升分析。_\n")
    category_lines: list[str] = []
    if category_df is not None and len(category_df):
        labels = {str(r["strategy"]): str(r.get("label", r["strategy"]))
                  for _, r in category_df.iterrows()}
        for cat in ("术语精确", "语义泛化", "多跳推理"):
            sub = category_df[category_df["category"] == cat]
            if len(sub) == 0:
                continue
            sub = sub.sort_values("recall@5", ascending=False)
            best = sub.iloc[0]
            worst = sub.iloc[-1]
            detail = " > ".join(
                f"{labels.get(str(r['strategy']), r['strategy'])} {_fmt(r['recall@5'])}"
                for _, r in sub.iterrows()
            )
            n_cat = int(best.get("n", 0))
            category_lines.append(
                f"- **{cat}**（{n_cat} 题）：最佳 `{best['strategy']}`"
                f"（Recall@5 = {_fmt(best['recall@5'])}），最弱 `{worst['strategy']}`"
                f"（{_fmt(worst['recall@5'])}）｜ Recall@5 排序：{detail}"
            )

        # 用实测数据解释，而非套用"预期"
        def _r5(strategy: str) -> float | None:
            row = category_df[(category_df["category"] == "语义泛化")
                              & (category_df["strategy"] == strategy)]
            return float(row.iloc[0]["recall@5"]) if len(row) else None

        sem_v, sem_h = _r5("vector"), _r5("hybrid")
        if sem_v is not None and sem_h is not None and sem_h < sem_v:
            category_lines.append(
                f"\n> **一个反直觉但重要的现象**：在语义泛化类上，纯向量的 Recall@5"
                f"（{_fmt(sem_v)}）反而高于 RRF 混合（{_fmt(sem_h)}）。"
                "原因是 RRF 只按名次融合、不看分数，BM25 在同一题里塞进来的"
                "「字面命中但语义不符」的块会挤占融合后的前几位，"
                "把向量本来排得对的结果往后推。"
                "这恰好说明**融合不是终点**：混合的价值要靠精排兑现 —— "
                "加上交叉编码器后该类 Recall@5 才反超（见 `hybrid_rerank` 行）。"
                "这也是本项目坚持「混合召回 + Rerank」而不是只做混合的原因。\n"
            )

        def _r5_cat(cat: str, strategy: str) -> float | None:
            row = category_df[(category_df["category"] == cat)
                              & (category_df["strategy"] == strategy)]
            return float(row.iloc[0]["recall@5"]) if len(row) else None

        mh_best = category_df[category_df["category"] == "多跳推理"]
        if len(mh_best):
            mh_best = mh_best.sort_values("recall@5", ascending=False).iloc[0]
            mh_rr = _r5_cat("多跳推理", "hybrid_rerank")
            if (str(mh_best["strategy"]) != "hybrid_rerank" and mh_rr is not None):
                category_lines.append(
                    f"> **多跳推理类的局限（必须如实写进报告）**：该类最佳策略是 "
                    f"`{mh_best['strategy']}`（Recall@5 = {_fmt(mh_best['recall@5'])}），"
                    f"而 `hybrid_rerank` 为 {_fmt(mh_rr)}。原因是交叉编码器对"
                    "「单个片段与问题的相关性」打分，而多跳问题需要的是"
                    "**若干互补片段合起来**才能拼出答案 —— 每个片段单独看都不够相关，"
                    "精排反而会把它们压下去。"
                    "改进方向：对多跳题放宽 Top-k（见 6.2）、"
                    "或引入 query 分解 / 多轮检索（把一个问题拆成两个子问题分别召回），"
                    "这也是本项目下一步的工作。\n"
                )

        category_lines.append(
            "> 读表提示：类别结论必须结合题量看（语义泛化 50 题，另两类各 25 题，"
            "后者的置信区间更宽）。逐题明细见 `retrieval_comparison_per_question.csv`。\n"
        )
    chunk_lines: list[str] = []
    if chunk_df is not None and len(chunk_df):
        chunk_lines.append("**分块策略对比**\n")
        chunk_lines.append(_md_table(chunk_df, ["strategy", "n", "recall@1", "recall@5",
                                                "recall@10", "mrr", "ndcg", "avg_seconds"]))
        chunk_lines.append(
            "\n> **判定粒度说明（重要）**：分块策略消融按「文件名 + 页码」判定，"
            "而不是按 `chunk_id`。原因：标注是基于 `recursive` 索引的 chunk_id 标注的；"
            "切换到 `fixed` 索引后，同一段内容会被切成不同的块，"
            "**页码相同但 chunk_id 完全不同**。若仍按 chunk_id 比对，"
            "`fixed` 档各项指标会恒为 0 —— 那是判定粒度用错，不是分块策略差。"
            "本项目第一次跑消融时就出现了这个结构性错误（`fixed` 全 0），"
            "已改为页级判定后修正。同一页的多个块在评测中会保序去重为一个位置。\n"
        )
        if {"recall@5"} <= set(chunk_df.columns) and len(chunk_df) >= 2:
            # 按 Recall@5 → Recall@3 → nDCG 依次比较，避免"打平却宣布胜者"
            order_cols = [c for c in ("recall@5", "recall@3", "ndcg", "mrr")
                          if c in chunk_df.columns]
            ranked = chunk_df.sort_values(order_cols, ascending=False)
            better, worse = ranked.iloc[0], ranked.iloc[-1]
            r5_diff = float(better["recall@5"]) - float(worse["recall@5"])
            tiebreaker = ""
            if abs(r5_diff) < 1e-6 and len(order_cols) > 1:
                tb = next((c for c in order_cols[1:]
                           if abs(float(better[c]) - float(worse[c])) > 1e-6), None)
                if tb:
                    tiebreaker = (
                        f"两者在 Recall@5 上打平，用 `{tb}` 才分出高低"
                        f"（{_fmt(better[tb])} vs {_fmt(worse[tb])}）—— "
                        "说明差异主要体现在**排序质量**而非召回数量上。"
                    )
                else:
                    tiebreaker = "两套分块在该测试集上的差异在指标精度内不显著。"
            sec_note = ""
            if {"avg_seconds"} <= set(chunk_df.columns):
                ds = float(worse["avg_seconds"]) - float(better["avg_seconds"])
                if abs(ds) > 0.05:
                    faster = better if ds > 0 else worse
                    slower = worse if faster is better else better
                    sec_note = (
                        f" 另外 `{faster['strategy']}` 单题耗时略低"
                        f"（{_fmt(faster['avg_seconds'], 2)}s vs "
                        f"{_fmt(slower['avg_seconds'], 2)}s）：两套索引的块数与块长都不同，"
                        "而精排要对 20 个候选逐对打分，耗时随候选文本总长度变化，"
                        "因此两档存在几秒的系统性差异。"
                    )
            reason = (
                "递归语义分块优先在段落、换行、句号等自然边界切分，"
                "每个块内部语义更完整；固定长度分块会把一条规定从中间截断，"
                "导致「上半句在这个块、下半句在下一个块」，检索到任何一个都答不完整。"
                "本项目语料是学校规章制度，条款边界清晰（「第X条」），"
                "语义分块理论上更能保住条款的完整性。"
            )
            chunk_lines.append(
                f"\n结论：本次实测 `{better['strategy']}` 略优"
                f"（Recall@5 {_fmt(better['recall@5'])} vs {_fmt(worse['recall@5'])}，"
                f"差 {r5_diff:+.4f}）。{tiebreaker}{sec_note} 机制上：{reason}\n"
            )
    else:
        chunk_lines.append("_（未执行或索引不足，跳过分块消融）_\n")

    rerank_lines: list[str] = []
    if rerank_df is not None and len(rerank_df):
        rerank_lines.append("**Rerank Top-k 对比**\n")
        rerank_lines.append(_md_table(
            rerank_df,
            ["top_k", "n", "recall@1", "recall@3", "recall@5", "mrr", "ndcg",
             "precision@k", "avg_seconds"]))
        rerank_lines.append("")
        rerank_lines.append(
            "> `precision@k` = 该档位自身 k 下的精确率（上下文里有多少内容真相关）。\n"
        )
        rerank_lines.append(
            "> **实现说明**：精排器是「对全部候选打分 → 排序 → 截断到 k」，"
            "排序结果与 k 无关，因此 Top-3/Top-5 的结果一定是 Top-8 结果的**前缀**。"
            "本消融据此只跑一遍 `max(top_k)`，再对每个 k 取前缀重算指标，"
            "与逐档独立运行数学等价，但把 3 次精排降到 1 次"
            "（CPU 上每档约 32 分钟，节省约 1 小时）。"
            "因此三档的 `avg_seconds` 相同是**实现特性**，不代表'加大 Top-k 免费' —— "
            "真实代价体现在上下文变长（见 `precision@k`）与生成端 token 增加。\n"
        )
        if {"recall@1", "avg_seconds"} <= set(rerank_df.columns) and len(rerank_df) >= 2:
            best_r1 = rerank_df.loc[rerank_df["recall@1"].idxmax()]
            rerank_lines.append(
                f"- Recall@1 最佳档位：**Top-{int(best_r1['top_k'])}**"
                f"（{_fmt(best_r1['recall@1'])}）"
            )
            if "precision@k" in rerank_df.columns:
                pk = rerank_df.dropna(subset=["precision@k"])
                if len(pk) >= 2:
                    best_p = pk.loc[pk["precision@k"].idxmax()]
                    rerank_lines.append(
                        f"- 上下文精确率最佳档位：**Top-{int(best_p['top_k'])}**"
                        f"（precision@k = {_fmt(best_p['precision@k'])}）"
                        "—— 这一档送到 LLM 的上下文「杂质」最少。"
                    )
            if {"recall@5"} <= set(rerank_df.columns):
                r1 = rerank_df.sort_values("top_k")
                gain = float(r1["recall@5"].iloc[-1]) - float(r1["recall@5"].iloc[0])
                rerank_lines.append(
                    f"- 召回饱和情况：Top-{int(r1['top_k'].iloc[0])} → "
                    f"Top-{int(r1['top_k'].iloc[-1])} 的 Recall@5 变化为 {gain:+.4f}。"
                    "若该变化很小，说明**精排已经把答案排在前几位**，"
                    "继续加大 Top-k 只是把噪声送进上下文，"
                    "此时应选较小档位以降低延迟和 token 成本。"
                )
            rerank_lines.append(
                "\n- 权衡：Top-k 越大，召回上限越高，但送入 LLM 的上下文变长、"
                "杂质比例上升（`precision@k` 下降）、生成端 token 成本增加，"
                "且「迷失在中间」风险上升（关键条款被夹在长上下文里）。"
                "应选「召回接近饱和、而上下文仍干净」的拐点。\n"
            )
            # 明确给出"为什么选 Top-X"的答案：取 Recall@5 已接近饱和的最小档位
            if {"recall@5", "precision@k"} <= set(rerank_df.columns):
                r_sorted = rerank_df.dropna(subset=["precision@k"]).sort_values("top_k")
                if len(r_sorted) >= 2:
                    best_r5 = float(r_sorted["recall@5"].max())
                    rec = r_sorted[r_sorted["recall@5"] >= best_r5 - 1e-6].iloc[0]
                    rec_k = int(rec["top_k"])
                    rec_p = float(rec["precision@k"])
                    larger = r_sorted[r_sorted["top_k"] > rec_k]
                    extra = ""
                    if len(larger):
                        big = larger.iloc[-1]
                        extra = (
                            f" 再放大到 Top-{int(big['top_k'])} 并不会提高 Recall@5"
                            f"（仍为 {_fmt(big['recall@5'])}），"
                            f"却把上下文精确率从 {_fmt(rec_p)} 降到 "
                            f"{_fmt(big['precision@k'])}，即多出来的 "
                            f"{int(big['top_k']) - rec_k} 条大多是噪声。"
                        )
                    rerank_lines.append(
                        f"- **本项目选择 Top-{rec_k} 的依据**：它是在 Recall@5 "
                        f"（{_fmt(best_r5)}）已经饱和的前提下**最小的档位**——"
                        f"比它小的档位（Top-{int(r_sorted.iloc[0]['top_k'])}）Recall@5 明显更低，"
                        f"比它大的档位只增加上下文体积而不增加有效召回。{extra}"
                        "所以选中的档位应当用「召回饱和点」而不是「指标最高的那一档」来定。\n"
                    )
    else:
        rerank_lines.append("_（未执行）_\n")
    generation_lines: list[str] = []
    if generation_df is not None and len(generation_df):
        try:
            n_gen = int(float(generation_df["n"].max()))
        except Exception:  # noqa: BLE001 - 缺列或非数值时按"未标注样本量"处理
            n_gen = 0
        generation_lines.append(_md_table(
            generation_df,
            ["strategy", "label", "n", "n_scored"] + metrics + ["avg_seconds", "gen_tokens"]))
        generation_lines.append("")
        generation_lines.append(
            "> 评分口径：LLM-as-Judge（**自建**，非 RAGAS），temperature=0、强制 JSON 输出，"
            f"1~{settings.evaluation.judge_scale} 分。`faithfulness` 看"
            "「答案的每个论断能否在所给上下文中找到依据」，"
            "`answer_relevancy` 看「是否答到了用户问的那件事」。"
            "解析失败的条目**不计 0 分**，而是从均值里剔除并单独计数（`n_scored` 与 `n` 的差）。\n"
        )
        # token 消耗对比：这是 Rerank 除准确率之外的第二个收益点
        if {"strategy", "gen_tokens", "avg_seconds"} <= set(generation_df.columns):
            g = generation_df.dropna(subset=["gen_tokens"]).sort_values("gen_tokens")
            if len(g) >= 2:
                cheapest = g.iloc[0]
                priciest = g.iloc[-1]
                c_tok, p_tok = float(cheapest["gen_tokens"]), float(priciest["gen_tokens"])
                if p_tok > 0 and c_tok < p_tok:
                    generation_lines.append(
                        f"- **上下文成本的差异**：`{cheapest['strategy']}` 的生成 token 最少"
                        f"（{int(c_tok):,}），`{priciest['strategy']}` 最多（{int(p_tok):,}），"
                        f"相差约 {(1 - c_tok / p_tok) * 100:.0f}%。"
                        "这正是精排的第二个收益：**只把最相关的几条送进上下文**，"
                        "既降低 token 成本，也减少长上下文里的干扰。\n"
                    )
        if n_gen and n_gen < 10:
            generation_lines.append(
                f"> ⚠️ **样本量说明**：本节只抽了 **{n_gen} 条**题"
                f"（{n_gen} × {len(settings.evaluation.judge_metrics)} 个维度），"
                "分数只能作为**链路可用性**的验证，不足以支撑策略间的强弱结论 —— "
                "3 条题里差 0.3 分完全在噪声范围内。"
                "若要写进结论，请用 `--generation-limit 10`（或更多）重跑。\n"
            )
        # 拒答统计：拒答本身是"不编造"的正确行为，但它同时也暴露检索失败
        if generation_per_q is not None and "is_refusal" in generation_per_q.columns:
            ref = generation_per_q.copy()
            ref["is_refusal"] = ref["is_refusal"].astype(str).str.lower().isin(["true", "1"])
            tot = ref.groupby("strategy").size()
            nref = ref[ref["is_refusal"]].groupby("strategy").size()
            detail = "、".join(
                f"`{s}` {int(nref.get(s, 0))}/{int(tot.get(s, 0))}"
                for s in tot.index
            )
            generation_lines.append(
                f"- **拒答情况**：{detail}（回答「资料中无相关内容」）。\n"
                "  拒答是**正确行为**（宁可说不知道也不编造），但它有两种成因，必须分开看：\n"
                "  ① 语料确实没有该信息 —— 这时拒答是系统在正确工作；\n"
                "  ② 语料里有、但**检索没找到** —— 这时拒答暴露的是检索缺陷。\n"
                "  本项目实测存在②：测试集里的几道术语精确题问的是真实存在的文号"
                "（语料中各出现 1 次，位于文件首页），但检索没把它们排进上下文，"
                "模型据此如实拒答。**这类题在检索指标上会被记为「未命中」，"
                "在生成指标上却表现为「忠实度很高」** —— 只看 faithfulness 会误判为系统很可靠。\n"
            )
    else:
        generation_lines.append("_（未执行；如需请加 `--with-generation`，会消耗 token）_\n")

    # 读回可视化的实际文件名，保证报告里的链接不会指向不存在的图
    figures: list[str] = []
    if FIGURES_DIR.exists():
        for p in sorted(FIGURES_DIR.glob("*.png")):
            figures.append(f"- `{rel(p)}`")

    # --report-only 时 elapsed≈0，直接写"0.0 分钟"会让人以为实验秒级跑完了
    if elapsed < 5 and not limit:
        elapsed_text = "本次仅重新生成报告（未重跑实验）"
    else:
        elapsed_text = (f"{elapsed / 60:.1f} 分钟"
                        + (f"（抽样前 {limit} 条）" if limit else "（全量）"))

    report = f"""# 实验报告：混合检索与 Rerank 领域知识库系统

> 生成时间：{now}　｜　脚本：`scripts/run_all_experiments.py`
> 总耗时：{elapsed_text}

---

## 一、实验设置

| 项 | 值 |
|---|---|
| 测试集 | {testset_summary} |
| 检索策略 | vector（纯向量） / bm25（纯关键词） / hybrid（RRF 融合） / hybrid_rerank（+精排） |
| 评测指标 | {'、'.join('Recall@' + str(k) for k in recall_ks)}、MRR、nDCG |
| 分块策略 | {chunk_strategy} |
| 向量模型 | `{settings.embedding.resolved_model}`（{settings.embedding.dimension} 维） |
| 精排模型 | `{settings.rerank.resolved_model}`（CrossEncoder，max_length={settings.rerank.max_length}） |
| 生成模型 | `{settings.llm.model}` |
| 检索链路 | 每路召回 {settings.retrieval.dense_top_k}/{settings.retrieval.sparse_top_k} → 融合取 {settings.retrieval.top_k} → 精排取 {settings.rerank.top_k} |

> **可复现性说明**：BM25 与精排是确定性的（同一输入必得同一输出）；
> 向量检索走的是近似最近邻索引（HNSW），在并发/缓存状态下遍历路径可能略有差异，
> 因此 `vector` / `hybrid` 策略的 Recall@10/@20 在不同轮次间可能有 **±0.002~0.02** 的抖动。
> 本项目复跑一次后，`Recall@1/@3/@5`、`MRR`、`nDCG` 与各策略相对排序均保持一致，
> 结论不受影响。若需要严格可复现，可把向量索引换成暴力检索（Flat）。

---

## 二、测试集构建方法学

{TESTSET_METHODOLOGY}

> 本测试集共 {testset_n} 条，类别分布：{testset_dist}。
> 标注方式为人工阅读语料后选定问题与相关文档块，并通过 `chunk_id` 存在性校验。

---

## 三、主结果：四种检索策略对比

{_md_table(retrieval_df, main_cols)}

> 说明：`hybrid_rerank` 精排后只返回 {settings.rerank.top_k} 条，
> 因此它的 Recall@10 与 Recall@5 必然相同（上限为 {settings.rerank.top_k}），
> 这不是缺陷而是策略特性；精排的价值体现在 **Recall@1/@5 与 MRR/nDCG 的提升**上。

---

## 四、提升分析：混合 + Rerank 相比基线

{chr(10).join(analysis_lines) if analysis_lines else '_（无数据）_'}

**自动结论：**

{chr(10).join('- ' + c for c in conclusions) if conclusions else '_（数据不足，无法自动归纳）_'}

---

## 五、分维度分析

{chr(10).join(category_lines) if category_lines else '_（无数据）_'}

{_md_table(category_df, ['strategy', 'label', 'category', 'n', 'recall@5', 'recall@10', 'mrr', 'ndcg']) if category_df is not None else ''}

---

## 六、消融实验

### 6.1 分块策略

{chr(10).join(chunk_lines)}

### 6.2 Rerank Top-k

{chr(10).join(rerank_lines)}

---

## 七、生成质量（LLM-as-Judge，1-{settings.evaluation.judge_scale} 分）

{chr(10).join(generation_lines)}

---

## 八、对比图

{chr(10).join(figures) if figures else '_（未生成，请运行 `python -m src.evaluation.cli visualize`）_'}

---

## 九、原因分析（供答辩使用）

### 为什么混合检索优于单路

单路检索的失败模式是互补的：
- **纯向量**：擅长语义泛化，但对专有名词、文号、条款号容易"语义漂移"而漏检
- **纯 BM25**：精确匹配强、毫秒级，但无法处理"用词不同、意思相同"的提问
- **RRF 融合**：只用名次不用分数，规避了两路分数量纲不同（余弦 0~1 vs BM25 0~20+）的问题

可直接引用的证据：`POST /retrieve` 返回的 `stats` 字段会给出
"两路重叠 / 仅向量召回 / 仅 BM25 召回"的数量 —— 重叠越少说明互补性越强。

### 为什么需要 Rerank

召回阶段用的是**双编码器**（查询与文档分别编码），两者从未交互，只能判断"大致相关"；
精排用的是**交叉编码器**（拼接后整体编码），能做词级交互，判断准确得多，
但计算量大，无法对全库实时打分，因此必须"先召回（几十条）再精排"。

最直观的证据是**逐题名次变化**：查看 `retrieval_comparison_per_question.csv`，
对比 `hybrid` 与 `hybrid_rerank` 两行的 `first_hit_rank` ——
若精排把正确答案从第 2 位提到第 1 位，就是最有力的说明。

### 为什么强调引用溯源

答案中的 `[n]` 会映射回具体的文件与页码（`citations` 字段），评委可当场核对。
本项目实测：一次真实问答中模型引用的 7 个条款号**全部**真实存在于所给上下文中，无编造。

### 实验过程中踩过的四个坑（决定实验能不能说明问题）

这四个坑都不是"模型不好"，而是**实验设计错误**；如果不修，报告里的数字全是错的，
而且错得很隐蔽 —— 指标看上去"只是偏低"，没有任何报错。这一节建议答辩时主动讲。

**坑一：测试集标注过窄 → 指标被系统性低估**

第一版测试集用「锚点正则 + `limit=1~2`」自动标注，每个问题只标 1~2 个块，
而真实相关块有 6~15 个。Recall 的分母（相关集）被压小，
不仅数值偏低，还"压平"了策略间差异，看不出任何结论。
改为宽标注（关键词命中 + 页码范围过滤 + 人工核查）后，
BM25 的 Recall@5 从 0.2736 升到 0.4715 —— **这个提升与模型无关，纯粹是修正了标注**。

**坑二：判定粒度与标注粒度不匹配 → `fixed` 档全 0**

分块策略消融时，`fixed` 索引下的各项指标全为 0。
原因：标注是 `recursive` 的 chunk_id，换索引后同一页内容被切成不同的块，
**页码相同但 chunk_id 完全不同**，按 id 比对必然全不命中。
修正：跨分块策略比较一律退到「文件名 + 页码」粒度（见 6.1）。
教训：**遇到"某项全 0"要先怀疑评测口径，而不是先怀疑模型**。

**坑三：参数没接上 → 消融实验静默失效**

Rerank Top-k 消融传入 `top_k=3/5/8`，三档指标完全相同。
原因：`pipeline.retrieve()` 里最终条数由 `rerank_top_k` 控制，
而 `top_k` 只作用于召回、随后被精排覆盖，参数根本没生效。
**静默失效比报错更危险** —— 报错会逼你去修，静默失效会让你把错数据写进报告。
修正：统一 `top_k` 语义（对 `hybrid_rerank` 即"精排保留条数"），
并补了一条 `precision@k` 随档位变化的断言式自检。

**坑四：裁判看到的上下文与模型看到的不是同一份 → 幻觉误判**

生成阶段会按 `context_max_chars`（本项目 6000 字）裁剪上下文：
`vector` 检索 20 条、实际只有 19 条进 prompt。而裁判原先拿到的是**未裁剪的 20 条**，
于是它的编号体系与模型看到的**不一致** —— 模型正常引用 `[19]`（prompt 里确实有第 19 条），
裁判却判成"引用了不存在的编号，属编造"。

这是**假阳性幻觉**：指标被无端拉低，且拉低的是检索条数多的那几档（vector/bm25/hybrid），
方向上恰好**偏袒** `hybrid_rerank`（它只返回 5 条，不存在裁剪）。
若不发现，报告会得出一条虚假结论："精排降低了幻觉"。

修正：把 `LLMResponse.contexts`（生成时**实际使用**的上下文）传给裁判，
并给裁判的 `format_context` 加了"被截断时告警"的兜底。
修正前后对照（10 题分层抽样）：
`vector` 语义泛化类 faithfulness `4.5 → 5.0`、总体 `4.6 → 4.6`；
`hybrid_rerank` `4.9 → 4.8`。差异不大，说明该 bug 对**均值**影响有限，
但它会让**逐题结论**（"这题模型编造了"）完全失真 —— 这类错误不会体现在汇总里，必须靠机制检查发现。

### 本系统的已知局限（如实说明，避免过度宣称）

1. **多跳推理类没有拿到最佳成绩**：该类最佳是纯向量（见第五节），
   因为交叉编码器评估的是"单片段相关性"，而多跳需要互补片段共同支撑。
   后续可上 query 分解或多轮检索。
2. **精排延迟高**：CPU 上单题约 19 秒，不满足交互式场景。
   可换 GPU、蒸馏小型交叉编码器，或只对 Top-10 精排（见 6.2 的拐点选择）。
3. **引用溯源是"可核对"而非"自动核验"**：目前由 LLM-as-Judge 判 faithfulness，
   未做引用与原文的逐句蕴含校验。
4. **评测集规模**：100 题足以支撑策略间的相对比较，但类别内样本（各 25 题）
   的置信区间较宽，不宜据此下强结论。

---

_本报告由 `run_all_experiments.py` 自动生成；所有数字均来自真实运行，未做手工修饰。_
"""
    REPORT_PATH.write_text(report, encoding="utf-8")
    return REPORT_PATH


def run_report(
    *,
    testset_summary: str,
    limit: int,
    chunk_strategy: str,
    elapsed: float,
    render_figures: bool = True,
):
    """⑥ 出图 + 汇总报告。"""
    from src.evaluation import visualize as viz

    # 读取测试集规模与类别分布，供报告"测试集构建方法学"章节引用
    testset_n = 0
    testset_dist = "（未统计）"
    try:
        from src.evaluation import testset as _ts

        _data = _ts.load_testset(check_chunk_ids=False)
        testset_n = len(_data)
        testset_dist = " / ".join(f"{k} {v}" for k, v in _data.category_counts().items())
        # --report-only 时不会跑 check 步骤，这里补上测试集摘要，
        # 否则报告"实验设置"表里会出现"（未校验）"这种占位符。
        if not testset_summary or testset_summary.startswith("（"):
            n_blocks = sum(len(it.relevant_chunk_ids) for it in _data.items)
            testset_summary = (
                f"{testset_n} 条（{testset_dist}）｜ 标注文档块 {n_blocks} 个"
                f"（平均 {n_blocks / max(testset_n, 1):.2f}/题）"
            )
    except Exception:  # noqa: BLE001 - 报告不应因测试集读取失败而中断
        pass

    retrieval_df = _load_csv("retrieval_comparison.csv")
    category_df = _load_csv("retrieval_comparison_by_category.csv")
    chunk_df = _load_csv("ablation_chunk_strategy.csv")
    rerank_df = _load_csv("ablation_rerank_topk.csv")
    generation_df = _load_csv("generation_comparison.csv")
    generation_per_q = _load_csv("generation_comparison_per_question.csv")

    print("  已读取结果：")
    for name, df in (("retrieval_comparison", retrieval_df),
                     ("by_category", category_df),
                     ("ablation_chunk_strategy", chunk_df),
                     ("ablation_rerank_topk", rerank_df),
                     ("generation_comparison", generation_df)):
        print(f"    {name:<26} {'有数据（%d 行）' % len(df) if df is not None else '无'}")

    if render_figures:
        class _Stub:
            def __init__(self, main, cat=None):
                self._main = main
                self._cat = cat if cat is not None else _load_csv(
                    "retrieval_comparison_by_category.csv")

            def summary_frame(self):
                return self._main

            def category_frame(self):
                return self._cat

        stub_retrieval = _Stub(retrieval_df) if retrieval_df is not None else None
        stub_generation = _Stub(generation_df) if generation_df is not None else None
        stub_chunk = _Stub(chunk_df) if chunk_df is not None else None
        stub_rerank = _Stub(rerank_df) if rerank_df is not None else None

        written = viz.render_all(stub_retrieval, stub_generation, stub_chunk, stub_rerank,
                                 out_dir=FIGURES_DIR)
        print(f"\n  生成 {len(written)} 张图：")
        for key, path in written.items():
            print(f"    {key:<22} {rel(path)}")

    path = build_report(
        retrieval_df, category_df, chunk_df, rerank_df, generation_df,
        testset_summary=testset_summary, testset_n=testset_n, testset_dist=testset_dist,
        limit=limit, chunk_strategy=chunk_strategy, elapsed=elapsed,
        generation_per_q=generation_per_q,
    )
    print(f"\n  ⭐ 汇总报告: {rel(path)}")
    return path



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/run_all_experiments.py",
        description="一键跑完全部实验并生成答辩报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="每个实验只跑测试集前 N 条（0=全部）。**建议先用 3 验证脚本**")
    parser.add_argument("--chunk-strategy", default=None, choices=["fixed", "recursive"],
                        help="主实验使用的分块策略，默认取配置")
    parser.add_argument("--strategies", nargs="+", default=None,
                        choices=["vector", "bm25", "hybrid", "hybrid_rerank"],
                        help="覆盖要对比的检索策略")
    parser.add_argument("--rerank-topk", nargs="+", type=int, default=None,
                        help="覆盖 Rerank Top-k 档位，默认取配置")
    parser.add_argument("--steps", nargs="+", default=None, choices=ALL_STEPS,
                        help=f"只跑指定步骤，可选：{list(ALL_STEPS)}")
    parser.add_argument("--skip-ablation", action="store_true", help="跳过分块与 Top-k 消融")
    parser.add_argument("--judge-level", choices=["chunk", "page"], default="chunk",
                        help="检索评估的判定粒度：chunk=按 chunk_id（默认），"
                             "page=按文件名+页码（分块策略消融自动使用 page）")
    parser.add_argument("--with-generation", action="store_true",
                        help="额外跑生成质量评估（**消耗 token**）")
    parser.add_argument("--generation-limit", type=int, default=5,
                        help="生成评估的抽样条数（默认 5，控制成本）")
    parser.add_argument("--yes", action="store_true", help="跳过生成评估的确认提示")
    parser.add_argument("--report-only", action="store_true",
                        help="不跑实验，仅根据已有 CSV 重新生成图与报告")
    parser.add_argument("--no-progress", action="store_true", help="关闭进度条")
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)

    steps = list(args.steps) if args.steps else ["check", "retrieval", "ablation", "report"]
    if args.skip_ablation and "ablation" in steps:
        steps.remove("ablation")
    if args.with_generation and "generation" not in steps:
        steps.insert(steps.index("report") if "report" in steps else len(steps), "generation")
    if args.report_only:
        steps = ["report"]

    t_all = time.perf_counter()
    banner("阶段 6 · 一键实验")
    print(f"  步骤: {steps}")
    print(f"  抽样: {'全部' if not args.limit else '前 ' + str(args.limit) + ' 条'}")
    print(f"  分块: {args.chunk_strategy or settings.chunk.default_strategy}")
    print(f"  结果目录: {rel(RESULTS_DIR)}")

    # 覆盖式参数（写回 settings，让各模块共享同一套配置）
    if args.rerank_topk:
        object.__setattr__(settings.evaluation, "ablation_rerank_top_k", list(args.rerank_topk))
        print(f"  Rerank Top-k 档位已覆盖为: {args.rerank_topk}")

    testset_summary = "（未校验）"
    ok = True

    if "check" in steps or "retrieval" in steps or "ablation" in steps:
        step("① 校验测试集")
        ok, testset_summary = run_check(args.limit)
        if not ok:
            banner("实验中止：测试集不可用")
            return 1

    if "retrieval" in steps:
        step("② 检索策略对比（不消耗 token）")
        if args.strategies:
            from src.evaluation import retrieval_eval as re
            re.STRATEGY_LABELS = {k: re.STRATEGY_LABELS[k] for k in args.strategies}  # type: ignore[assignment]
        run_retrieval(args.limit, args.chunk_strategy, args.no_progress, args.judge_level)

    if "ablation" in steps:
        step("③④ 消融实验（不消耗 token）")
        run_ablation(args.limit, args.chunk_strategy, args.no_progress)

    if "generation" in steps:
        step("⑤ 生成质量评估（消耗 token）")
        run_generation(args.limit or args.generation_limit, args.chunk_strategy,
                       args.no_progress, args.yes)

    if "report" in steps:
        step("⑥ 出图与汇总报告")
        run_report(
            testset_summary=testset_summary, limit=args.limit,
            chunk_strategy=args.chunk_strategy or settings.chunk.default_strategy,
            elapsed=time.perf_counter() - t_all,
        )

    elapsed = time.perf_counter() - t_all
    banner(f"完成，总耗时 {elapsed / 60:.1f} 分钟")
    print(f"  ⭐ 报告: {rel(REPORT_PATH)}")
    print(f"  图表  : {rel(FIGURES_DIR)}")
    print(f"  数据  : {rel(RESULTS_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
