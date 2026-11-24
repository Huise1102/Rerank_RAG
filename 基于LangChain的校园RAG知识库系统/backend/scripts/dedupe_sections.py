"""按章节对同一知识库内的重复附件做归并清理。

同一章节在多份文档中重复出现时，保留文本量最大的副本，删除其余副本的分块，
并同步刷新版本分块统计、BM25 索引与去重缓存。

用法：
    python scripts/dedupe_sections.py --kb-id 4            # 预演，只打印
    python scripts/dedupe_sections.py --kb-id 4 --apply    # 实际删除
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from langchain_core.documents import Document as LcDocument  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.db.models import Document, DocumentVersion  # noqa: E402
from src.db.session import async_session_factory, engine  # noqa: E402
from src.retrieval import bm25, canonical  # noqa: E402
from src.retrieval.vector_store import get_vector_store  # noqa: E402

MAX_COMPARE_CHARS = 30000


def _jaccard(left: str, right: str) -> float:
    left_grams = {left[index : index + 2] for index in range(len(left) - 1)}
    right_grams = {right[index : index + 2] for index in range(len(right) - 1)}
    union = left_grams | right_grams
    if not union:
        return 0.0
    return len(left_grams & right_grams) / len(union)


def _collect(store) -> list[dict]:
    data = store.get(include=["documents", "metadatas"])
    rows = []
    for store_id, text, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        meta = meta or {}
        doc_id = int(meta.get("document_id") or 0)
        if doc_id <= 0:
            continue
        rows.append(
            {
                "id": store_id,
                "doc_id": doc_id,
                "version_id": int(meta.get("version_id") or 0),
                "chunk_type": str(meta.get("chunk_type") or ""),
                "section": canonical._section_key(text or "", meta),
                "text": text or "",
                "normalized": canonical._normalize(text or ""),
                "metadata": meta,
            }
        )
    return rows


def _plan(rows: list[dict], threshold: float) -> tuple[list[str], list[str]]:
    grouped: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["section"]][row["doc_id"]].append(row)

    removed: list[str] = []
    decisions: list[str] = []
    for section, by_doc in sorted(grouped.items()):
        if len(by_doc) < 2:
            continue
        totals = {doc: sum(len(item["normalized"]) for item in items) for doc, items in by_doc.items()}
        canonical_doc = max(totals, key=lambda doc: (totals[doc], -doc))
        canonical_text = "".join(
            item["normalized"] for item in by_doc[canonical_doc]
        )[:MAX_COMPARE_CHARS]
        for doc_id, items in by_doc.items():
            if doc_id == canonical_doc:
                continue
            text = "".join(item["normalized"] for item in items)[:MAX_COMPARE_CHARS]
            similarity = _jaccard(canonical_text, text)
            if similarity < threshold:
                decisions.append(
                    f"跳过 {section}: doc{doc_id} 与 doc{canonical_doc} 相似度 {similarity:.3f} 低于阈值"
                )
                continue
            removed.extend(item["id"] for item in items)
            decisions.append(
                f"删除 {section}: doc{doc_id} {len(items)} 块，"
                f"保留 doc{canonical_doc} {len(by_doc[canonical_doc])} 块（相似度 {similarity:.3f}）"
            )
    return removed, decisions


async def _refresh_counts(rows: list[dict]) -> dict[int, int]:
    version_counts: dict[int, int] = defaultdict(int)
    version_tables: dict[int, int] = defaultdict(int)
    version_clauses: dict[int, int] = defaultdict(int)
    doc_counts: dict[int, int] = defaultdict(int)
    for row in rows:
        version_counts[row["version_id"]] += 1
        doc_counts[row["doc_id"]] += 1
        if row["chunk_type"] == "table":
            version_tables[row["version_id"]] += 1
        elif row["chunk_type"] == "clause":
            version_clauses[row["version_id"]] += 1

    async with async_session_factory() as db:
        versions = (
            await db.execute(
                select(DocumentVersion).where(DocumentVersion.id.in_(list(version_counts)))
            )
        ).scalars().all()
        for version in versions:
            version.chunk_count = version_counts.get(version.id, 0)
            version.table_count = version_tables.get(version.id, 0)
            version.clause_count = version_clauses.get(version.id, 0)
        documents = (
            await db.execute(select(Document).where(Document.id.in_(list(doc_counts))))
        ).scalars().all()
        for document in documents:
            document.chunk_count = doc_counts.get(document.id, 0)
        await db.commit()
    return doc_counts


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-id", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    store = get_vector_store(args.kb_id)
    rows = _collect(store)
    removed, decisions = _plan(rows, args.threshold)

    print(f"知识库 {args.kb_id} 当前分块 {len(rows)}，计划删除 {len(removed)}")
    for line in decisions:
        print("  " + line)

    if not args.apply or not removed:
        print("预演结束，未修改数据")
        await engine.dispose()
        return

    store.delete(ids=removed)
    remaining = _collect(store)
    counts = await _refresh_counts(remaining)
    bm25.save_documents(
        args.kb_id,
        [
            LcDocument(page_content=row["text"], metadata=row["metadata"])
            for row in remaining
        ],
    )
    bm25.invalidate(args.kb_id)
    canonical.invalidate(args.kb_id)
    await engine.dispose()

    print(f"删除完成，剩余分块 {len(remaining)}")
    for doc_id, count in sorted(counts.items()):
        print(f"  doc{doc_id}: {count} 块")


if __name__ == "__main__":
    asyncio.run(main())
