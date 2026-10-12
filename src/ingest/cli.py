"""
阶段 1 —— 数据入库命令行入口。

用法::

    # 1) 解析 PDF 并分块（两种策略都生成）
    python -m src.ingest.cli load
    python -m src.ingest.cli load --strategy fixed

    # 2) 构建索引（向量 + BM25）
    python -m src.ingest.cli index --strategy recursive

    # 3) 一键完成 load + index
    python -m src.ingest.cli all --strategy recursive

    # 4) 查看索引现状
    python -m src.ingest.cli stats

    # 5) 检索自测（不依赖阶段 2 代码，仅验证索引可用）
    python -m src.ingest.cli search "混合检索的作用" --strategy recursive
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以 python -m src.ingest.cli 方式运行，也允许直接 python cli.py
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import PROJECT_ROOT, settings  # noqa: E402
from src.ingest import indexer, loader, splitter  # noqa: E402


def _setup_console() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _print_header(title: str) -> None:
    print("=" * 84)
    print(f" {title}")
    print("=" * 84)


def _resolve_strategies(args: argparse.Namespace, default_all: bool) -> list[str]:
    """统一解析策略参数：--strategies（可重复） > --strategy > 配置默认；
    default_all=True 时（load/index 命令）默认返回全部策略。
    """
    explicit = getattr(args, "strategies", None)
    if explicit:
        return list(explicit)
    single = getattr(args, "strategy", None)
    if single:
        return [single]
    if default_all:
        return list(settings.chunk.available_strategies)
    return [settings.chunk.default_strategy]


def cmd_load(args: argparse.Namespace) -> int:
    """解析 PDF -> 清洗 -> 分块 -> 写 data/processed/"""
    _print_header("阶段 1 · 步骤 1/2：PDF 解析与分块")

    files = loader.iter_pdf_files(args.raw_dir)
    if not files:
        print(f"⚠️  {settings.paths.raw_dir} 下没有 PDF 文件。")
        print("   请放入待入库的 PDF 后重试。")
        return 1

    print(f"发现 {len(files)} 个 PDF：")
    for f in files:
        print(f"  - {f.relative_to(settings.paths.raw_dir) if args.raw_dir is None else f.name}")

    print(f"\n解析引擎: {settings.pdf.engine}")
    pages, report = loader.load_pdfs(args.raw_dir, progress=None)

    print(f"\n[解析结果] {report.summary()}")
    if report.skipped:
        print("[跳过文件]")
        for name, why in report.skipped:
            print(f"  - {name}: {why}")

    print("\n[页眉页脚检测]")
    for fname, hfs in report.header_footers.items():
        print(f"  {fname}:")
        print(loader.describe_headers_footers(hfs))

    if args.show_removed and pages:
        print("\n[清洗删除的行示例（前 15 条）]")
        shown = 0
        for pg in pages:
            for line in pg.removed:
                if shown >= 15:
                    break
                print(f"  {pg.source} p{pg.page}: {line[:70]}")
                shown += 1
            if shown >= 15:
                break

    if not pages:
        print("\n⚠️  所有页面清洗后均为空，无可分块内容。")
        return 1

    documents = loader.to_documents(pages)
    print(f"\n转为 Document: {len(documents)} 页")

    strategies = _resolve_strategies(args, default_all=True)
    for strategy in strategies:
        result = splitter.split_documents(documents, strategy=strategy, show_progress=not args.no_progress)
        path = splitter.save_chunks(
            result,
            strategy=strategy,
            source_files=[p.name for p in files],
        )
        print(f"\n  {result.summary()}")
        print(f"  参数: {result.params}")
        print(f"  已写入: {path.relative_to(PROJECT_ROOT)}")

    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """读取分块 -> 向量化入 Chroma -> 建 BM25"""
    _print_header("阶段 1 · 步骤 2/2：构建索引")

    strategies = _resolve_strategies(args, default_all=True)

    # 模型只加载一次供所有策略复用：重复加载既慢，也可能触发联网校验
    embedder = None
    if not args.skip_vector:
        try:
            embedder, device = indexer.load_embedder()
            print(f"\n>>> Embedding 模型已加载 (device={device})，将复用于所有策略")
        except RuntimeError as exc:
            print(f"\n⚠️  无法加载 Embedding 模型：\n{exc}")
            return 1

    for strategy in strategies:
        print(f"\n>>> 策略: {strategy}")
        if args.strategy_file:
            print(f"    使用分块文件: {args.strategy_file}")
        try:
            report = indexer.build_index(
                strategy,
                rebuild=not args.no_rebuild,
                embedder=embedder,
                batch_size=args.batch_size,
                show_progress=not args.no_progress,
                source=args.strategy_file,
            )
        except FileNotFoundError as exc:
            print(f"    ⚠️  {exc}")
            return 1
        print(f"    {report.summary()}")

    return 0


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_load(args)
    if rc != 0:
        return rc
    return cmd_index(args)


def cmd_stats(args: argparse.Namespace) -> int:
    _print_header("阶段 1 · 索引现状")
    print(f"分块文件目录: {settings.paths.processed_dir}")
    for path in splitter.list_processed():
        print(f"  - {path.name} ({path.stat().st_size / 1024:.1f} KB)")
    if not splitter.list_processed():
        print("  （暂无，请先执行 load）")

    print(f"\n向量库目录: {settings.paths.chroma_dir}")
    print(f"BM25 目录  : {settings.paths.bm25_dir}")
    for strategy in settings.chunk.available_strategies:
        info = indexer.index_stats(strategy)
        print(
            f"\n[{strategy}]\n"
            f"  分块文件  : {info['chunks_file']} ({info['chunks']} 块)\n"
            f"  Chroma    : {info['collection']} ({info['vector_count']} 条)\n"
            f"  BM25      : {info['bm25_file']} ({info['bm25_count']} 条)"
        )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """仅用 BM25 做检索自测，确认索引与还原链路可用。"""
    _print_header(f"阶段 1 · 索引自测（BM25）strategy={args.strategy}")
    index = indexer.load_bm25_index(args.strategy)
    print(f"语料 {len(index)} 块 | 分词器 {index.meta.get('tokenizer')} | 构建于 {index.meta.get('built_at')}")
    print(f"查询: {args.query}\n")

    hits = index.search(args.query, top_k=args.top_k)
    if not hits:
        print("（无命中）")
        return 0
    for rank, (i, score) in enumerate(hits, 1):
        doc = index.documents[i]
        meta = doc.get("metadata", {})
        text = doc["text"].replace("\n", " ")
        print(f"[{rank}] score={score:.3f}  {meta.get('source')} p{meta.get('page')}  {meta.get('chunk_id')}")
        print(f"    {text[:150]}{'...' if len(text) > 150 else ''}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.ingest.cli",
        description="阶段 1 数据入库：PDF 解析、清洗、分块、建索引",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    def add_no_progress(p: argparse.ArgumentParser) -> None:
        """--no-progress 放在子命令上：Argparse 不允许同名选项同时存在于父子解析器。"""
        p.add_argument("--no-progress", action="store_true", help="关闭进度条")

    def add_strategy(p: argparse.ArgumentParser, multiple: bool = False) -> None:
        if multiple:
            # --strategy 指定单一策略，--strategies 可重复；命令实现统一读 args.strategies
            # （缺省时回退到 args.strategy）
            p.add_argument(
                "--strategy", default=None, choices=["fixed", "recursive"],
                help="分块策略，默认两种都做",
            )
            p.add_argument(
                "--strategies", action="append", choices=["fixed", "recursive"],
                help="分块策略，可重复指定以覆盖 --strategy",
            )
        else:
            p.add_argument(
                "--strategy", default=None, choices=["fixed", "recursive"],
                help="分块策略，默认取 config.chunk.default_strategy",
            )

    p_load = sub.add_parser("load", help="解析 PDF 并分块")
    add_strategy(p_load, multiple=True)
    add_no_progress(p_load)
    p_load.add_argument("--raw-dir", default=None, help="PDF 目录，默认 data/raw")
    p_load.add_argument("--show-removed", action="store_true", help="打印被清洗删除的行")
    p_load.set_defaults(func=cmd_load)

    p_index = sub.add_parser("index", help="构建向量索引与 BM25 索引")
    add_strategy(p_index, multiple=True)
    add_no_progress(p_index)
    p_index.add_argument("--batch-size", type=int, default=None, help="向量化批大小")
    p_index.add_argument("--no-rebuild", action="store_true", help="不删除已有集合（增量追加）")
    p_index.add_argument("--strategy-file", default=None, help="直接指定分块 JSON 文件")
    p_index.add_argument("--skip-vector", action="store_true", help="只建 BM25 索引（跳过向量化）")
    p_index.set_defaults(func=cmd_index)

    p_all = sub.add_parser("all", help="load + index 一键完成")
    add_strategy(p_all, multiple=True)
    add_no_progress(p_all)
    p_all.add_argument("--raw-dir", default=None)
    p_all.add_argument("--show-removed", action="store_true")
    p_all.add_argument("--batch-size", type=int, default=None)
    p_all.add_argument("--no-rebuild", action="store_true")
    p_all.add_argument("--strategy-file", default=None)
    p_all.add_argument("--skip-vector", action="store_true", help="只建 BM25 索引")
    p_all.set_defaults(func=cmd_all)

    p_stats = sub.add_parser("stats", help="查看索引现状")
    p_stats.set_defaults(func=cmd_stats)

    p_search = sub.add_parser("search", help="BM25 检索自测")
    add_strategy(p_search)
    add_no_progress(p_search)
    p_search.add_argument("query", help="查询文本")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    # 补齐各命令可能未声明的属性，简化命令实现
    if not hasattr(args, "strategies"):
        args.strategies = None
    if not hasattr(args, "no_progress"):
        args.no_progress = False
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
