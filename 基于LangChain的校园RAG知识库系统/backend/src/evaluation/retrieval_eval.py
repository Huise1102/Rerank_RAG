from datetime import datetime, timezone

from src.db.models import StrategyPreset
from src.evaluation.metrics import (
    aggregate,
    hit_at_k,
    keyword_coverage,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from src.retrieval.retrievers import build_retriever
from src.retrieval.scope import RetrievalScope


def build_eval_scope(role: str = "admin", department_id: int | None = None) -> RetrievalScope:
    allow_department = role in {"staff", "dept_admin", "admin"} and (
        department_id is not None or role == "admin"
    )
    return RetrievalScope(
        user_id=None,
        role=role,
        department_id=department_id,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
        allow_public=True,
        allow_department=allow_department,
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _markdown(kind: str, summary: dict, rows: list[dict]) -> str:
    lines = [
        f"# {kind} 评估报告",
        "",
        f"- 样本数：{summary['total']}",
        f"- 命中率：{summary['hit_rate']}",
        f"- Recall@k：{summary['recall']}",
        f"- Precision@k：{summary['precision']}",
        f"- MRR：{summary['mrr']}",
        f"- nDCG@k：{summary['ndcg']}",
        f"- 关键词覆盖率：{summary['keyword_coverage']}",
        "",
        "| 编号 | 问题 | 命中 | Recall | MRR | nDCG |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        question = str(row["question"]).replace("|", "／")
        lines.append(
            f"| {row['id']} | {question} | {row['hit']} | {row['recall']} | "
            f"{row['mrr']} | {row['ndcg']} |"
        )
    return "\n".join(lines)


async def evaluate_retrieval(
    items: list[dict],
    kb_id: int,
    preset: StrategyPreset | None,
    top_k: int = 10,
    limit: int | None = None,
    role: str = "admin",
    department_id: int | None = None,
) -> dict:
    selected = items[:limit] if limit else items
    scope = build_eval_scope(role, department_id)
    bundle = build_retriever(kb_id, preset, scope)
    rows: list[dict] = []
    for item in selected:
        documents = await bundle.retriever.ainvoke(str(item["question"]))
        document_ids = _unique([str(doc.metadata.get("document_id")) for doc in documents])
        chunk_ids = _unique([str(doc.metadata.get("chunk_id")) for doc in documents])
        relevant_docs = {str(value) for value in item.get("relevant_doc_ids", [])}
        relevant_chunks = {str(value) for value in item.get("relevant_chunk_ids", [])}
        relevant = relevant_chunks or relevant_docs
        ranked = chunk_ids if relevant_chunks else document_ids
        text = "\n".join(doc.page_content for doc in documents)
        rows.append(
            {
                "id": item.get("id"),
                "question": item.get("question"),
                "category": item.get("category"),
                "hit": hit_at_k(ranked, relevant, top_k),
                "recall": round(recall_at_k(ranked, relevant, top_k), 4),
                "precision": round(precision_at_k(ranked, relevant, top_k), 4),
                "mrr": round(mrr(ranked, relevant), 4),
                "ndcg": round(ndcg_at_k(ranked, relevant, top_k), 4),
                "keyword_coverage": round(
                    keyword_coverage(text, item.get("expected_keywords", [])), 4
                ),
                "retrieved_documents": document_ids[:top_k],
                "retrieved_chunks": chunk_ids[:top_k],
            }
        )

    summary = {
        "total": len(rows),
        "hit_rate": aggregate([row["hit"] for row in rows]),
        "recall": aggregate([row["recall"] for row in rows]),
        "precision": aggregate([row["precision"] for row in rows]),
        "mrr": aggregate([row["mrr"] for row in rows]),
        "ndcg": aggregate([row["ndcg"] for row in rows]),
        "keyword_coverage": aggregate([row["keyword_coverage"] for row in rows]),
        "top_k": top_k,
        "role": role,
        "department_id": department_id,
        "strategy": preset.name if preset else "系统默认",
    }
    return {
        "kind": "retrieval",
        "summary": summary,
        "rows": rows,
        "markdown": _markdown("检索", summary, rows),
    }
