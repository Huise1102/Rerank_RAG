"""
阶段 5 —— 评估命令行入口。

用法::

    # 1) 校验测试集（标注完成后先跑这个）
    python -m src.evaluation.cli check

    # 2) 生成标注候选（含 chunk_id 与摘要，供人工标注）
    python -m src.evaluation.cli candidates --out data/testset.draft.json

    # 3) 检索策略对比（不消耗 LLM token）
    python -m src.evaluation.cli retrieval
    python -m src.evaluation.cli retrieval --limit 10        # 小样本先验证

    # 4) 生成质量评估（消耗 token，务必先小样本）
    python -m src.evaluation.cli generation --limit 3

    # 5) 消融实验
    python -m src.evaluation.cli ablation
    python -m src.evaluation.cli ablation --rerank-topk 3 5 8 --limit 5

    # 6) 画图（读取已有 CSV 结果）
    python -m src.evaluation.cli visualize --from-results

    # 7) 一键：检索评估 + 消融 + 出图（不含生成评估，避免误耗 token）
    python -m src.evaluation.cli all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import PROJECT_ROOT, settings  # noqa: E402
from src.evaluation import ablation as ab  # noqa: E402
from src.evaluation import retrieval_eval as re  # noqa: E402
from src.evaluation import testset as ts  # noqa: E402
from src.evaluation import visualize as viz  # noqa: E402


def _setup_console() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _header(title: str) -> None:
    print("=" * 90)
    print(f" {title}")
    print("=" * 90)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)

# 子命令


def cmd_check(args: argparse.Namespace) -> int:
    _header("阶段 5 · 测试集校验")
    target = ts.find_testset_file(args.path)
    print(f"测试集文件: {target}")
    if not target.exists():
        print("\n⚠️  文件不存在。\n")
        print(ts._missing_testset_hint(target))
        return 1

    try:
        data = ts.load_testset(target, validate=True, check_chunk_ids=not args.no_index_check)
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ 加载失败：{type(exc).__name__}: {exc}")
        return 1

    print(f"\n[概览] {data.summary()}")
    counts = data.category_counts()
    if counts:
        print("[类别分布]")
        for cat in ts.CATEGORIES:
            n = counts.get(cat, 0)
            bar = "█" * n
            print(f"  {cat:<6} {n:>3}  {bar}")
        missing = [c for c in ts.CATEGORIES if not counts.get(c)]
        if missing:
            print(f"  ⚠️  以下类别还没有样本：{missing}（建议每类至少 5 条，否则分维度结论不稳固）")

    print("\n[校验结果]")
    print(ts.format_issues(data))
    return 0 if data.is_valid else 1


def cmd_candidates(args: argparse.Namespace) -> int:
    _header("阶段 5 · 生成标注候选")
    try:
        path = ts.save_annotation_candidates(
            args.out, chunk_strategy=args.chunk_strategy,
            per_file=args.per_file, max_chars=args.max_chars,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 生成失败：{type(exc).__name__}: {exc}")
        print("   请先完成数据入库：python -m src.ingest.cli all")
        return 1
    print(f"已写入: {_rel(path)}")
    print(
        "\n下一步：\n"
        "  1) 打开该文件，为每行写一个真实问题（question）\n"
        "  2) 只保留真正相关的 chunk_id，删除其余\n"
        "  3) 为每行选一个 category（术语精确 / 语义泛化 / 多跳推理）\n"
        "  4) 把需要的条目复制到 data/testset.json（顶层为数组，去掉 _ 开头的字段）\n"
        "  5) 运行 python -m src.evaluation.cli check 校验"
    )
    return 0


def cmd_retrieval(args: argparse.Namespace) -> int:
    _header("阶段 5 · 检索策略对比评估")
    try:
        data = ts.load_testset(args.path)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ {exc}")
        return 1
    if not data.is_valid:
        print("❌ 测试集校验未通过：")
        print(ts.format_issues(data))
        return 1

    print(f"[测试集] {data.summary()}")
    strategies = args.strategies or list(re.STRATEGY_LABELS)
    judge_level = getattr(args, "judge_level", "chunk")
    print(f"[策略] {strategies}")
    print(f"[规模] {'前 ' + str(args.limit) + ' 条' if args.limit else '全部 ' + str(len(data)) + ' 条'}"
          f" ｜ 分块策略={args.chunk_strategy or settings.chunk.default_strategy}"
          f" ｜ 判定粒度={judge_level}")
    print("提示：hybrid_rerank 在 CPU 上每题约 18~21 秒\n")

    result = re.evaluate_all(
        data, strategies=strategies, chunk_strategy=args.chunk_strategy,
        limit=args.limit, show_progress=not args.no_progress,
        judge_level=judge_level,
    )

    print("\n[主对比表]")
    print(result.markdown_table())
    cat = result.category_frame()
    if not cat.empty:
        print("\n[分维度对比]")
        try:
            print(cat.to_markdown(index=False))
        except Exception:  # noqa: BLE001
            print(cat.to_string(index=False))

    written = re.save_retrieval_results(result)
    print("\n[已导出]")
    for key, path in written.items():
        print(f"  {key:<12} {_rel(path)}")
    return 0


def cmd_generation(args: argparse.Namespace) -> int:
    _header("阶段 5 · 生成质量评估（LLM-as-Judge）")
    from src.evaluation import generation_eval as ge

    try:
        data = ts.load_testset(args.path)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ {exc}")
        return 1
    if not data.is_valid:
        print("❌ 测试集校验未通过：")
        print(ts.format_issues(data))
        return 1

    n = args.limit or settings.evaluation.generation_sample or len(data)
    n = min(n, len(data))
    strategies = args.strategies or list(re.STRATEGY_LABELS)
    print(f"[测试集] {data.summary()}")
    print(f"[规模] {n} 条 × {len(strategies)} 策略 = **{n * len(strategies)} 次生成 + 同量裁判调用**")
    print(f"[裁判] {settings.evaluation.judge_model} ｜ 维度 {settings.evaluation.judge_metrics}")
    print("⚠️  这会消耗较多 token，建议先用 --limit 2 验证\n")

    if not args.yes:
        reply = input("确认继续？(yes/no) > ").strip().lower()
        if reply not in ("y", "yes"):
            print("已取消。")
            return 1

    result = ge.evaluate_generation(
        data, strategies=strategies, chunk_strategy=args.chunk_strategy,
        limit=args.limit, show_progress=not args.no_progress,
    )
    print("\n[主对比表]")
    print(result.markdown_table())
    cat = result.category_frame()
    if not cat.empty:
        print("\n[分维度]")
        try:
            print(cat.to_markdown(index=False))
        except Exception:  # noqa: BLE001
            print(cat.to_string(index=False))

    written = ge.save_generation_results(result)
    print("\n[已导出]")
    for key, path in written.items():
        print(f"  {key:<12} {_rel(path)}")
    return 0


def cmd_ablation(args: argparse.Namespace) -> int:
    _header("阶段 5 · 消融实验")
    try:
        data = ts.load_testset(args.path)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ {exc}")
        return 1
    if not data.is_valid:
        print("❌ 测试集校验未通过：")
        print(ts.format_issues(data))
        return 1

    print(f"[测试集] {data.summary()}")
    ready = ab.available_chunk_strategies()
    print(f"[可用分块索引] {ready or '无（请先运行 python -m src.ingest.cli all）'}")

    chunk_result = None
    rerank_result = None

    if args.kind in ("chunk", "all"):
        print("\n--- 消融一：分块策略（固定长度 vs 递归语义）---")
        chunk_result = ab.ablation_chunk_strategies(
            data, limit=args.limit, show_progress=not args.no_progress,
        )
        print(chunk_result.markdown_table())

    if args.kind in ("rerank", "all"):
        print("\n--- 消融二：Rerank Top-k ---")
        print(f"档位: {args.rerank_topk or settings.evaluation.ablation_rerank_top_k}")
        n_q = args.limit or len(data)
        print(f"规模: {n_q} 题（单次精排 + 前缀截断，总耗时≈跑 1 档，约 "
              f"{n_q * 19 / 60:.0f} 分钟）\n")
        rerank_result = ab.ablation_rerank_top_k(
            data, top_k_values=args.rerank_topk, chunk_strategy=args.chunk_strategy,
            limit=args.limit, show_progress=not args.no_progress,
        )
        print(rerank_result.markdown_table())

    written = ab.save_ablation_results(chunk_result, rerank_result)
    print("\n[已导出]")
    for key, path in written.items():
        print(f"  {key:<12} {_rel(path)}")
    return 0


def cmd_visualize(args: argparse.Namespace) -> int:
    _header("阶段 5 · 生成图表")
    import pandas as pd

    directory = settings.evaluation.result_dir
    figures = settings.evaluation.figure_dir

    def _load(frame_cls: str, csv_name: str):
        """从 CSV 还原一个轻量结果对象，供绘图使用。"""
        path = directory / csv_name
        if not path.exists():
            print(f"  （跳过 {csv_name}：文件不存在）")
            return None
        df = pd.read_csv(path, encoding="utf-8-sig")

        class _Stub:
            def summary_frame(self_inner):
                return df

            def category_frame(self_inner):
                cat_path = directory / csv_name.replace(".csv", "_by_category.csv")
                if cat_path.exists():
                    return pd.read_csv(cat_path, encoding="utf-8-sig")
                return pd.DataFrame()

        return _Stub()

    print(f"读取结果目录: {_rel(directory)}")
    if not directory.exists():
        print("❌ 结果目录不存在，请先运行 retrieval / generation / ablation")
        return 1

    retrieval = _load("", "retrieval_comparison.csv")
    generation = _load("", "generation_comparison.csv")
    chunk = _load("", "ablation_chunk_strategy.csv")
    rerank = _load("", "ablation_rerank_topk.csv")

    written = viz.render_all(
        retrieval, generation, chunk, rerank, out_dir=figures,
    )
    if not written:
        print("\n❌ 没有生成任何图（缺少结果 CSV，请先跑评估）")
        return 1
    print("\n[已生成图表]")
    for key, path in written.items():
        print(f"  {key:<22} {_rel(path)}")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    """一键：检索评估 + 消融 + 出图（刻意不含生成评估，避免误耗 token）。"""
    rc = cmd_retrieval(args)
    if rc != 0:
        return rc
    rc = cmd_ablation(args)
    if rc != 0:
        print("\n（消融阶段出现问题，仍尝试出图）")
    rc2 = cmd_visualize(args)
    print("\n提示：生成质量评估需单独运行（会消耗 token）：")
    print("  python -m src.evaluation.cli generation --limit 3")
    return 0 if rc2 == 0 else rc2

# 解析器


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.evaluation.cli",
        description="阶段 5 评估：测试集校验、检索对比、生成质量、消融、可视化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, *, strategies: bool = True) -> None:
        p.add_argument("--path", default=None, help="测试集路径，默认自动查找")
        p.add_argument("--chunk-strategy", default=None, choices=["fixed", "recursive"],
                       help="分块策略，默认取配置")
        p.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
        p.add_argument("--no-progress", action="store_true", help="关闭进度条")
        if strategies:
            p.add_argument("--strategies", nargs="+", default=None,
                           choices=["vector", "bm25", "hybrid", "hybrid_rerank"],
                           help="要对比的检索策略，默认全部四种")
            p.add_argument("--judge-level", choices=["chunk", "page"], default="chunk",
                           help="判定粒度：chunk=按 chunk_id（默认），"
                                "page=按文件名+页码（跨分块策略比较时用）")

    p_check = sub.add_parser("check", help="校验测试集")
    p_check.add_argument("--path", default=None)
    p_check.add_argument("--no-index-check", action="store_true",
                         help="跳过 chunk_id 是否存在于索引的校验")
    p_check.set_defaults(func=cmd_check)

    p_cand = sub.add_parser("candidates", help="生成标注候选（辅助人工标注）")
    p_cand.add_argument("--out", default="data/testset.draft.json")
    p_cand.add_argument("--chunk-strategy", default=None, choices=["fixed", "recursive"])
    p_cand.add_argument("--per-file", type=int, default=0, help="每个文件最多列多少块（0=全部）")
    p_cand.add_argument("--max-chars", type=int, default=160)
    p_cand.set_defaults(func=cmd_candidates)

    p_ret = sub.add_parser("retrieval", help="检索策略对比评估")
    add_common(p_ret)
    p_ret.set_defaults(func=cmd_retrieval)

    p_gen = sub.add_parser("generation", help="生成质量评估（消耗 token）")
    add_common(p_gen)
    p_gen.add_argument("--yes", action="store_true", help="跳过确认直接执行")
    p_gen.set_defaults(func=cmd_generation)

    p_abl = sub.add_parser("ablation", help="消融实验")
    add_common(p_abl)
    p_abl.add_argument("--kind", default="all", choices=["chunk", "rerank", "all"])
    p_abl.add_argument("--rerank-topk", nargs="+", type=int, default=None,
                       help="Rerank Top-k 档位，默认取配置（3 5 8）")
    p_abl.set_defaults(func=cmd_ablation)

    p_vis = sub.add_parser("visualize", help="根据已有结果 CSV 生成图表")
    p_vis.add_argument("--from-results", action="store_true",
                       help="（默认行为）从 data/eval/results 读取")
    p_vis.set_defaults(func=cmd_visualize)

    p_all = sub.add_parser("all", help="一键：检索评估 + 消融 + 出图")
    add_common(p_all)
    p_all.add_argument("--kind", default="all", choices=["chunk", "rerank", "all"])
    p_all.add_argument("--rerank-topk", nargs="+", type=int, default=None)
    p_all.add_argument("--from-results", action="store_true")
    p_all.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    # 补齐各命令可能未声明的属性
    for attr, default in (("limit", 0), ("no_progress", False), ("chunk_strategy", None),
                          ("strategies", None), ("path", None), ("from_results", False)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
