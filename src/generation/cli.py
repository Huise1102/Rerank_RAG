"""
阶段 3 —— 生成模块命令行入口。

用法::

    # 端到端问答（检索 + 生成）
    python -m src.generation.cli ask "学校规章制度的适用范围是怎么规定的？"

    # 只组装 prompt 不调用 LLM（无需 API Key，用于检查上下文与引用编号）
    python -m src.generation.cli prompt "学校规章制度的适用范围是怎么规定的？"

    # 流式输出（逐字打印）
    python -m src.generation.cli ask "学生违纪的处理流程" --stream

    # 检查 LLM 配置状态
    python -m src.generation.cli status
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import settings  # noqa: E402
from src.generation import citation as cit  # noqa: E402
from src.generation.llm import LLMClient  # noqa: E402
from src.generation.prompt import PromptBuilder  # noqa: E402


def _setup_console() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _header(title: str) -> None:
    print("=" * 84)
    print(f" {title}")
    print("=" * 84)


def _retrieve(query: str, strategy: str, chunk_strategy: str | None):
    from src.retrieval.pipeline import retrieve

    return retrieve(query, strategy=strategy, chunk_strategy=chunk_strategy, write_log=False)


def cmd_ask(args: argparse.Namespace) -> int:
    _header("阶段 3 · 检索 + 生成")
    retrieval = _retrieve(args.query, args.retrieval_strategy, args.chunk_strategy)
    print(f"[检索] {retrieval.summary()}")
    for hit in retrieval.results:
        print(f"  [{hit.rank}] {hit.final_score:.4f} {hit.citation()}  {hit.preview(52)}")
    print()

    client = LLMClient()
    ready, why = client.is_ready()
    print(f"[LLM] {why}")
    if not ready:
        print("\n请先在 .env 中配置 RAG_LLM__API_KEY，或用 `cli prompt` 子命令在无 Key 情况下检查 prompt。")
        return 1

    print(f"[生成] 模型={client.model} temperature={settings.llm.temperature}\n")
    if args.stream:
        stream, box = client.stream_response(args.query, retrieval.results)
        for delta in stream:
            print(delta, end="", flush=True)
        print("\n")
        response = box["response"]
    else:
        response = client.generate(args.query, retrieval.results)
        print(response.answer)
        print()

    print("-" * 84)
    print(f"[统计] {response.summary()}")
    report = response.citations
    print(f"[引用] {report.summary()}")
    for label in report.labels():
        print(f"  {label}")
    if report.invalid_indices:
        print(f"  ⚠️ 模型引用了不存在的编号：{report.invalid_indices}")
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    _header("阶段 3 · 仅组装 prompt（不调用 LLM）")
    retrieval = _retrieve(args.query, args.retrieval_strategy, args.chunk_strategy)
    print(f"[检索] {retrieval.summary()}\n")

    built = PromptBuilder().build(args.query, retrieval.results)
    print(f"[prompt] {built.summary()}\n")
    for msg in built.messages:
        print(f"----- {msg['role'].upper()} -----")
        print(msg["content"][: args.max_chars])
        if len(msg["content"]) > args.max_chars:
            print(f"...（共 {len(msg['content'])} 字符，已截断显示）")
        print()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _header("阶段 3 · LLM 配置状态")
    client = LLMClient()
    ready, why = client.is_ready()
    for key, value in client.info().items():
        print(f"  {key:<16}: {value}")
    print(f"\n  {'✅ 就绪' if ready else '⚠️  未就绪'} —— {why}")
    if not ready:
        print("\n  配置方式：在项目根目录新建 .env，写入 RAG_LLM__API_KEY=sk-你的密钥")
        print("  申请地址：https://platform.deepseek.com/api_keys")
    return 0 if ready else 1


def cmd_citations(args: argparse.Namespace) -> int:
    """离线检查引用解析：对任意文本解析 [n] 标记（不需要检索与 LLM）。"""
    _header("阶段 3 · 引用解析检查")
    text = args.text or sys.stdin.read()
    markers, invalid = cit.parse_markers(text)
    print(f"原文：{text}\n")
    print(f"解析出的编号：{markers}")
    print(f"是否拒答：{cit.is_refusal(text)}")
    print(f"去掉标记后：{cit.strip_markers(text)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.generation.cli",
        description="阶段 3 生成模块：检索 + 大模型生成 + 引用溯源",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("query", help="用户问题")
        p.add_argument(
            "--retrieval-strategy", default="hybrid_rerank",
            choices=["vector", "bm25", "hybrid", "hybrid_rerank"],
            help="检索策略，默认 hybrid_rerank",
        )
        p.add_argument("--chunk-strategy", default=None, choices=["fixed", "recursive"],
                       help="分块策略，默认取配置")

    p_ask = sub.add_parser("ask", help="端到端问答（检索 + 生成）")
    add_common(p_ask)
    p_ask.add_argument("--stream", action="store_true", help="流式输出")
    p_ask.set_defaults(func=cmd_ask)

    p_prompt = sub.add_parser("prompt", help="只组装 prompt（无需 API Key）")
    add_common(p_prompt)
    p_prompt.add_argument("--max-chars", type=int, default=2000, help="每条消息最多显示的字符数")
    p_prompt.set_defaults(func=cmd_prompt)

    p_status = sub.add_parser("status", help="查看 LLM 配置状态")
    p_status.set_defaults(func=cmd_status)

    p_cit = sub.add_parser("citations", help="离线检查引用解析")
    p_cit.add_argument("text", nargs="?", default=None, help="待解析文本，省略则从标准输入读取")
    p_cit.set_defaults(func=cmd_citations)

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
