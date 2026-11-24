"""给已有索引里的表格行 chunk 补上表名，避免同名表格在精排时混淆。

只改 chunk 文本与向量，不重跑解析，因此不会恢复已删除的重复副本。

用法：
    python scripts/patch_table_caption.py --kb-id 4          # 预演，只打印
    python scripts/patch_table_caption.py --kb-id 4 --apply  # 实际写入
"""

import argparse
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from langchain_core.documents import Document  # noqa: E402

from src.ingest.splitter import _context_prefix  # noqa: E402
from src.retrieval import bm25, canonical  # noqa: E402
from src.retrieval.vector_store import get_embeddings, get_vector_store  # noqa: E402

BATCH_SIZE = 32


def _patched_text(text: str, metadata: dict) -> str:
    caption = str(metadata.get("table_caption") or "").strip()
    if not caption or caption in (text or ""):
        return ""
    base = _context_prefix(metadata)
    if base and text.startswith(base):
        rest = text[len(base) :].lstrip("\n")
        return f"{base} · {caption}\n{rest}"
    return f"{caption}\n{text}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    store = get_vector_store(args.kb_id)
    payload = store.get(include=["documents", "metadatas"])
    ids: list[str] = []
    texts: list[str] = []
    samples: list[tuple[str, str]] = []
    for chunk_id, text, metadata in zip(
        payload.get("ids", []), payload.get("documents", []), payload.get("metadatas", [])
    ):
        metadata = dict(metadata or {})
        if metadata.get("chunk_type") not in {"table", "table_row"}:
            continue
        new_text = _patched_text(text or "", metadata)
        if not new_text:
            continue
        ids.append(chunk_id)
        texts.append(new_text)
        if len(samples) < 3:
            samples.append((text or "", new_text))

    print(f"待补表名的 chunk：{len(ids)}")
    for old, new in samples:
        print("-" * 60)
        print("原：", old[:120].replace("\n", " | "))
        print("新：", new[:120].replace("\n", " | "))

    if not args.apply or not ids:
        print("预演结束，加 --apply 才会写入。")
        return 0

    embeddings = get_embeddings()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors.extend(embeddings.embed_documents(texts[start : start + BATCH_SIZE]))
    store._collection.update(ids=ids, documents=texts, embeddings=vectors)

    documents: list[Document] = []
    refreshed = store.get(include=["documents", "metadatas"])
    for text, metadata in zip(refreshed.get("documents", []), refreshed.get("metadatas", [])):
        if text:
            documents.append(Document(page_content=text, metadata=dict(metadata or {})))
    bm25.save_documents(args.kb_id, documents)
    bm25.invalidate(args.kb_id)
    canonical.invalidate(args.kb_id)
    print(f"已更新 {len(ids)} 个 chunk，并重建 BM25（{len(documents)} 块）。")
    return 0


if __name__ == "__main__":
    os.chdir(BACKEND_ROOT)
    raise SystemExit(main())
