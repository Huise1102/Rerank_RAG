"""诊断索引构建过程中的卡死点。

用法：``.venv\\Scripts\\python.exe scripts\\diagnose_index.py recursive --timeout 180``
"""

from __future__ import annotations

import argparse
import faulthandler
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="索引构建卡死诊断")
    parser.add_argument("strategy", nargs="?", default="recursive", choices=["fixed", "recursive"])
    parser.add_argument("--timeout", type=int, default=180, help="超时秒数，超时后转储全部线程堆栈")
    parser.add_argument("--skip-vector", action="store_true", help="跳过向量索引（只测 BM25）")
    parser.add_argument("--skip-bm25", action="store_true", help="跳过 BM25（只测向量索引）")
    args = parser.parse_args()

    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    # 超时后自动转储全部线程堆栈，便于定位卡死点
    faulthandler.enable()
    faulthandler.dump_traceback_later(args.timeout, exit=True)
    print(f"[诊断] 策略={args.strategy} 超时={args.timeout}s（超时将自动转储堆栈并退出）")

    from config import settings
    from src.ingest import indexer, splitter

    t0 = time.time()
    result = splitter.load_chunks(args.strategy)
    print(f"[1] 读取分块: {result.total} 块 ({time.time() - t0:.1f}s)")

    embedder = None
    if not args.skip_vector:
        t0 = time.time()
        embedder, device = indexer.load_embedder()
        print(f"[2] 加载 embedding 模型: device={device} ({time.time() - t0:.1f}s)")

        t0 = time.time()
        texts = [c.text for c in result.chunks]
        vecs = indexer.embed_texts(embedder, texts, show_progress=False)
        print(f"[3] 向量化 {len(texts)} 块 -> shape={getattr(vecs, 'shape', '?')} ({time.time() - t0:.1f}s)")

        t0 = time.time()
        stats = indexer.build_vector_index(
            result.chunks, args.strategy, embedder=embedder, rebuild=True, show_progress=False
        )
        print(f"[4] 写入 Chroma: {stats} ({time.time() - t0:.1f}s)")

    if not args.skip_bm25:
        t0 = time.time()
        _, path = indexer.build_bm25_index(result.chunks, args.strategy, show_progress=False)
        print(f"[5] BM25 索引: {path.name} ({time.time() - t0:.1f}s)")

    faulthandler.cancel_dump_traceback_later()
    print("[完成] 全部步骤正常结束，未发生卡死")
    return 0


if __name__ == "__main__":
    sys.exit(main())
