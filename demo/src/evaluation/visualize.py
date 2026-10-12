"""结果可视化：matplotlib 画对比柱状图，输出到 ``data/eval/results/figures/``。

matplotlib 默认字体不含中文字形，中文标签会变成方框并有警告；这里在 Windows 上按
``Microsoft YaHei / SimHei / Noto Sans CJK / Source Han Sans / WenQuanYi`` 顺序探测
可用字体并显式设置，探测失败时**自动切换为英文标签**（由 ``L()`` 决定），保证图仍可用。

统一约定：无界面后端 ``Agg``（不弹窗，服务器上也能跑）、输出 PNG（``dpi=160``）、
``bbox_inches="tight"``、数值同时标在柱子上。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 优先尝试的中文字体（Windows 优先，其次跨平台开源字体）
_CJK_FONT_CANDIDATES = (
    "Microsoft YaHei", "SimHei", "Microsoft JhengHei",
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
    "WenQuanYi Zen Hei", "PingFang SC", "Heiti SC", "Arial Unicode MS",
)

#: 配色（与前端主题一致的蓝紫青体系）
_PALETTE = ("#2563eb", "#7c3aed", "#0ea5e9", "#f59e0b", "#10b981", "#ef4444")

_font_ready: bool | None = None


def _setup_matplotlib() -> bool:
    """
    配置 matplotlib：缓存目录 + 无界面后端 + 中文字体。

    :returns: 是否成功启用中文字体（False 时调用方应改用英文标签）
    """
    global _font_ready
    if _font_ready is not None:
        return _font_ready

    # matplotlib 默认把字体缓存写到 ~/.matplotlib，受限环境下会被拒绝
    # （"Could not save font_manager cache ... Permission denied"）；
    # 改到工作区内的目录，既能写、又不污染用户主目录。
    import os

    cache_dir = settings.paths.data_dir.parent / ".mplcache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    except Exception as exc:  # noqa: BLE001 - 缓存目录失败不应影响出图
        logger.debug("设置 MPLCONFIGDIR 失败: %s", exc)

    import matplotlib

    matplotlib.use("Agg")  # 必须在 pyplot 之前设置
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((name for name in _CJK_FONT_CANDIDATES if name in available), None)

    if chosen:
        matplotlib.rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"]
        _font_ready = True
        logger.info("画图使用中文字体: %s", chosen)
    else:
        # 找不到中文字体：不报错，改用英文标签（由调用方通过 L() 切换）
        matplotlib.rcParams["font.sans-serif"] = ["DejaVu Sans"]
        _font_ready = False
        logger.warning(
            "未找到可用的中文字体（尝试过 %s），图表将使用英文标签以避免乱码",
            ", ".join(_CJK_FONT_CANDIDATES),
        )

    matplotlib.rcParams["axes.unicode_minus"] = False  # 负号显示为方块的问题
    matplotlib.rcParams["figure.facecolor"] = "white"
    matplotlib.rcParams["savefig.facecolor"] = "white"
    return _font_ready


def _has_cjk() -> bool:
    return _setup_matplotlib()


def L(zh: str, en: str) -> str:
    """标签选择：有中文字体用中文，否则用英文。"""
    return zh if _has_cjk() else en



def _bar_with_values(ax: Any, x: Sequence[int], values: Sequence[float],
                     width: float, color: str, label: str) -> None:
    """画一组柱子并在柱顶标数值。"""
    bars = ax.bar([i for i in x], values, width=width, color=color, label=label,
                  edgecolor="white", linewidth=0.8)
    for rect, v in zip(bars, values):
        if v is None:
            continue
        ax.annotate(
            f"{v:.3f}", (rect.get_x() + rect.get_width() / 2, rect.get_height()),
            textcoords="offset points", xytext=(0, 3), ha="center", fontsize=7.5,
        )


def _finish(fig: Any, path: Path, tight: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight" if tight else None)
    import matplotlib.pyplot as plt

    plt.close(fig)
    logger.info("图已保存: %s", path)
    return path



def plot_strategy_comparison(
    result: Any,
    *,
    out_dir: str | Path | None = None,
    filename: str = "fig_strategy_comparison.png",
) -> Path | None:
    """
    检索策略对比图：每个策略一组柱子（Recall@1/5/10 + MRR + nDCG）。

    :param result: ``RetrievalEvalResult``
    """
    import matplotlib.pyplot as plt
    import numpy as np

    df = result.summary_frame()
    if df.empty:
        logger.warning("没有可画的数据（评估结果为空）")
        return None

    _setup_matplotlib()
    metrics = [c for c in ("recall@1", "recall@5", "recall@10", "mrr", "ndcg") if c in df.columns]
    labels = [m.upper().replace("RECALL@", "Recall@") for m in metrics]
    strategies = df["strategy"].tolist()
    x = np.arange(len(strategies))
    width = 0.8 / max(len(metrics), 1)

    fig, ax = plt.subplots(figsize=(max(8.0, 1.7 * len(strategies) + 3), 4.8))
    for i, metric in enumerate(metrics):
        values = [float(v) if v is not None else 0.0 for v in df[metric].tolist()]
        _bar_with_values(ax, x + i * width - 0.4 + width / 2, values, width,
                         _PALETTE[i % len(_PALETTE)],
                         L(metric.replace("recall@", "召回@").replace("mrr", "MRR").replace("ndcg", "nDCG"),
                           metric.upper()))

    ax.set_xticks(x)
    ax.set_xticklabels([s.replace("hybrid_rerank", L("混合+精排", "hybrid+rerank"))
                        .replace("hybrid", L("混合", "hybrid"))
                        .replace("vector", L("向量", "vector"))
                        .replace("bm25", "BM25") for s in strategies])
    ax.set_ylabel(L("指标值（越高越好）", "Score (higher is better)"))
    ax.set_title(L("检索策略对比（同一测试集）", "Retrieval strategy comparison"))
    ax.set_ylim(0, 1.08)
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, ncol=min(len(metrics), 5), loc="upper center",
              bbox_to_anchor=(0.5, -0.12))

    n = int(df["n"].iloc[0]) if "n" in df.columns and df["n"].notna().any() else 0
    ax.text(0.99, 0.97, L(f"测试集 {n} 条", f"n={n}"),
            transform=ax.transAxes, ha="right", va="top", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.3", fc="#f1f5f9", ec="#cbd5e1"))

    directory = Path(out_dir) if out_dir else settings.evaluation.figure_dir
    return _finish(fig, directory / filename)



def plot_category_breakdown(
    result: Any,
    *,
    metric: str = "recall@5",
    out_dir: str | Path | None = None,
    filename: str = "fig_category_breakdown.png",
) -> Path | None:
    """
    分维度对比图：横轴是类别，每组柱子是一个策略。

    预期术语精确类 BM25 占优、语义泛化类向量占优、多跳类混合+精排占优。
    """
    import matplotlib.pyplot as plt
    import numpy as np

    df = result.category_frame()
    if df.empty or metric not in df.columns:
        logger.warning("没有可画的分维度数据（metric=%s）", metric)
        return None

    _setup_matplotlib()
    categories = sorted(df["category"].unique().tolist())
    strategies = sorted(df["strategy"].unique().tolist())
    x = np.arange(len(categories))
    width = 0.8 / max(len(strategies), 1)

    fig, ax = plt.subplots(figsize=(max(8.0, 1.6 * len(categories) + 3.2), 4.8))
    for i, strategy in enumerate(strategies):
        sub = df[df["strategy"] == strategy].set_index("category")
        values = [float(sub.loc[c, metric]) if c in sub.index and sub.loc[c, metric] is not None
                  else 0.0 for c in categories]
        _bar_with_values(ax, x + i * width - 0.4 + width / 2, values, width,
                         _PALETTE[i % len(_PALETTE)], strategy)

    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel(metric.upper())
    ax.set_title(L(f"分维度对比（{metric}）", f"Per-category comparison ({metric})"))
    ax.set_ylim(0, 1.08)
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, ncol=min(len(strategies), 4), loc="upper center",
              bbox_to_anchor=(0.5, -0.12))

    directory = Path(out_dir) if out_dir else settings.evaluation.figure_dir
    return _finish(fig, directory / filename)



def plot_rerank_topk(
    result: Any,
    *,
    out_dir: str | Path | None = None,
    filename: str = "fig_rerank_topk.png",
) -> Path | None:
    """
    Rerank Top-k 消融图：左轴召回类指标、右轴 ``precision@k``。

    看点是"召回随 Top-k 上升、精确率同步下降"的权衡，用于支撑"为什么选 Top-5"。

    .. note::
       右轴不用"平均耗时"：本消融是单次精排 + 前缀截断（排序与 k 无关），三档耗时必然
       相同，画成平线会误导成"加大 Top-k 免费"。真实代价是上下文变长，体现在
       ``precision@k`` 下降上。
    """
    import matplotlib.pyplot as plt

    df = result.summary_frame()
    if df.empty:
        logger.warning("没有可画的 Top-k 消融数据")
        return None

    _setup_matplotlib()
    ks = df["top_k"].tolist()
    fig, ax = plt.subplots(figsize=(8.2, 4.8))

    metrics = [c for c in ("recall@1", "recall@3", "recall@5", "mrr", "ndcg") if c in df.columns]
    for i, metric in enumerate(metrics):
        values = [float(v) if v is not None else 0.0 for v in df[metric].tolist()]
        ax.plot(ks, values, marker="o", linewidth=2, markersize=6,
                color=_PALETTE[i % len(_PALETTE)], label=metric.upper())
        for kx, vy in zip(ks, values):
            ax.annotate(f"{vy:.3f}", (kx, vy), textcoords="offset points",
                        xytext=(0, 6), ha="center", fontsize=7.5)

    ax.set_xlabel(L("Rerank 保留条数 Top-k", "Rerank top-k"))
    ax.set_ylabel(L("召回/排序指标", "Recall / ranking metric"))
    ax.set_title(L("Rerank Top-k 消融：召回与上下文精确率的权衡",
                   "Rerank top-k ablation: recall vs context precision"))
    ax.set_xticks(ks)
    ax.grid(alpha=0.25, linestyle="--")
    ax.set_axisbelow(True)

    # 右轴：该档位自身 k 下的精确率 —— 上下文里有多少内容真相关
    if "precision@k" in df.columns and df["precision@k"].notna().any():
        ax2 = ax.twinx()
        prec = [float(v) if v is not None else 0.0 for v in df["precision@k"].tolist()]
        ax2.plot(ks, prec, marker="s", linewidth=1.8, linestyle=":",
                 color="#ef4444", label=L("precision@k（上下文精确率）", "precision@k"))
        ax2.set_ylabel(L("precision@k（送进上下文的内容有多少真相关）",
                         "precision@k"), color="#b91c1c")
        ax2.tick_params(axis="y", labelcolor="#b91c1c")
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=8, ncol=3, loc="upper center",
                  bbox_to_anchor=(0.5, -0.13))
    else:
        ax.legend(fontsize=8, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.13))

    directory = Path(out_dir) if out_dir else settings.evaluation.figure_dir
    return _finish(fig, directory / filename)



def plot_generation_scores(
    result: Any,
    *,
    out_dir: str | Path | None = None,
    filename: str = "fig_generation_scores.png",
) -> Path | None:
    """生成质量对比图：各策略在 faithfulness / answer_relevancy 上的平均分。"""
    import matplotlib.pyplot as plt
    import numpy as np

    df = result.summary_frame()
    if df.empty:
        logger.warning("没有可画的生成评估数据")
        return None

    _setup_matplotlib()
    metrics = [m for m in settings.evaluation.judge_metrics if m in df.columns]
    if not metrics:
        logger.warning("生成评估结果里没有裁判分数列")
        return None

    strategies = df["strategy"].tolist()
    x = np.arange(len(strategies))
    scale = settings.evaluation.judge_scale
    width = 0.8 / max(len(metrics), 1)

    fig, ax = plt.subplots(figsize=(max(7.5, 1.8 * len(strategies) + 2.6), 4.8))
    for i, metric in enumerate(metrics):
        values = [float(v) if v is not None else 0.0 for v in df[metric].tolist()]
        _bar_with_values(ax, x + i * width - 0.4 + width / 2, values, width,
                         _PALETTE[i % len(_PALETTE)],
                         L(f"{metric}（1-{scale}分）", f"{metric} (1-{scale})"))

    ax.set_xticks(x)
    ax.set_xticklabels([s.replace("hybrid_rerank", L("混合+精排", "hybrid+rerank"))
                        .replace("hybrid", L("混合", "hybrid"))
                        .replace("vector", L("向量", "vector"))
                        .replace("bm25", "BM25") for s in strategies])
    ax.set_ylabel(L(f"平均分（满分 {scale}）", f"Mean score (max {scale})"))
    ax.set_title(L("生成质量对比（LLM 裁判）", "Generation quality (LLM-as-Judge)"))
    ax.set_ylim(0, scale + 0.6)
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))

    n = int(df["n_scored"].iloc[0]) if "n_scored" in df.columns and df["n_scored"].notna().any() else 0
    ax.text(0.99, 0.97, L(f"评分条数 {n}", f"scored n={n}"),
            transform=ax.transAxes, ha="right", va="top", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.3", fc="#f1f5f9", ec="#cbd5e1"))

    directory = Path(out_dir) if out_dir else settings.evaluation.figure_dir
    return _finish(fig, directory / filename)



def plot_chunk_ablation(
    result: Any,
    *,
    out_dir: str | Path | None = None,
    filename: str = "fig_chunk_ablation.png",
) -> Path | None:
    """分块策略消融图：固定长度 vs 递归语义。"""
    import matplotlib.pyplot as plt
    import numpy as np

    df = result.summary_frame()
    if df.empty:
        logger.warning("没有可画的分块消融数据")
        return None

    _setup_matplotlib()
    metrics = [c for c in ("recall@1", "recall@5", "recall@10", "mrr", "ndcg") if c in df.columns]
    strategies = df["strategy"].tolist()
    x = np.arange(len(strategies))
    width = 0.8 / max(len(metrics), 1)

    fig, ax = plt.subplots(figsize=(max(7.5, 1.8 * len(strategies) + 2.6), 4.8))
    for i, metric in enumerate(metrics):
        values = [float(v) if v is not None else 0.0 for v in df[metric].tolist()]
        _bar_with_values(ax, x + i * width - 0.4 + width / 2, values, width,
                         _PALETTE[i % len(_PALETTE)], metric.upper())

    ax.set_xticks(x)
    # 结果键形如 "fixed+hybrid_rerank"：检索策略在消融时固定，图上只体现分块策略，
    # 全写进标签只会挤在一起。
    ax.set_xticklabels([
        s.split("+")[0].replace("fixed", L("固定长度分块", "fixed"))
        .replace("recursive", L("递归语义分块", "recursive"))
        for s in strategies
    ])
    ax.set_ylabel(L("指标值", "Score"))
    ax.set_title(L("分块策略消融（检索链路固定为混合+精排；页级判定）",
                   "Chunking ablation (hybrid+rerank, page-level judging)"))
    ax.set_ylim(0, 1.08)
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, ncol=min(len(metrics), 5), loc="upper center",
              bbox_to_anchor=(0.5, -0.12))

    directory = Path(out_dir) if out_dir else settings.evaluation.figure_dir
    return _finish(fig, directory / filename)



def render_all(
    retrieval_result: Any | None = None,
    generation_result: Any | None = None,
    chunk_ablation: Any | None = None,
    rerank_ablation: Any | None = None,
    *,
    out_dir: str | Path | None = None,
) -> dict[str, Path]:
    """
    把所有可用结果一次画完。

    只画"有数据"的图，缺失的跳过并打印原因，不会中断其余图的生成。
    """
    directory = Path(out_dir) if out_dir else settings.evaluation.figure_dir
    written: dict[str, Path] = {}

    tasks = [
        ("strategy_comparison", plot_strategy_comparison, retrieval_result),
        ("category_breakdown", plot_category_breakdown, retrieval_result),
        ("generation_scores", plot_generation_scores, generation_result),
        ("chunk_ablation", plot_chunk_ablation, chunk_ablation),
        ("rerank_topk", plot_rerank_topk, rerank_ablation),
    ]
    for name, func, data in tasks:
        if data is None:
            logger.info("跳过 %s：无数据", name)
            continue
        try:
            path = func(data, out_dir=directory)
            if path:
                written[name] = path
        except Exception as exc:  # noqa: BLE001 - 单张图失败不影响其它图
            logger.warning("生成 %s 图失败：%s", name, exc)
    return written
