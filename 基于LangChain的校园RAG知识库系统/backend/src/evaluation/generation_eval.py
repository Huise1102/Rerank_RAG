from src.db.models import StrategyPreset
from src.evaluation.judge import judge_answer
from src.evaluation.metrics import aggregate
from src.evaluation.retrieval_eval import build_eval_scope
from src.generation.chain import build_chain, format_context
from src.generation.llm import build_chat_model
from src.retrieval.retrievers import build_retriever


def _markdown(summary: dict, rows: list[dict]) -> str:
    lines = [
        "# 生成评估报告",
        "",
        f"- 样本数：{summary['total']}",
        f"- 忠实度：{summary['faithfulness']}",
        f"- 答案相关性：{summary['relevance']}",
        f"- 引用覆盖率：{summary['citation_coverage']}",
        "",
        "| 编号 | 问题 | 忠实度 | 相关性 | 引用数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        question = str(row["question"]).replace("|", "／")
        lines.append(
            f"| {row['id']} | {question} | {row['faithfulness']} | "
            f"{row['relevance']} | {row['citation_count']} |"
        )
    return "\n".join(lines)


async def evaluate_generation(
    items: list[dict],
    kb_id: int,
    preset: StrategyPreset | None,
    model_config: dict,
    limit: int | None = None,
    role: str = "admin",
    department_id: int | None = None,
) -> dict:
    selected = items[:limit] if limit else items
    scope = build_eval_scope(role, department_id)
    bundle = build_retriever(kb_id, preset, scope)
    model_name = str(
        model_config.get("ollama_model", "") if model_config.get("provider") == "ollama" else ""
    )
    chain = build_chain(build_chat_model(model_config), no_think=model_name.lower().startswith("qwen3"))
    rows: list[dict] = []
    for item in selected:
        documents = await bundle.retriever.ainvoke(str(item["question"]))
        answer = ""
        if documents:
            response = await chain.ainvoke(
                {
                    "context": format_context(documents),
                    "question": str(item["question"]),
                    "history": [],
                }
            )
            answer = str(getattr(response, "content", response)).strip()
        judged = await judge_answer(
            str(item["question"]),
            answer or "未检索到可核验依据",
            [document.page_content for document in documents],
            model_config,
        )
        rows.append(
            {
                "id": item.get("id"),
                "question": item.get("question"),
                "answer": answer[:500],
                "faithfulness": judged["faithfulness"],
                "relevance": judged["relevance"],
                "judge_reason": judged["reason"],
                "citation_count": len(documents),
                "retrieved_documents": [
                    str(document.metadata.get("document_id")) for document in documents
                ],
            }
        )

    evidence_rows = [row for row in rows if row["citation_count"] > 0]
    summary = {
        "total": len(rows),
        "faithfulness": aggregate([row["faithfulness"] for row in rows]),
        "relevance": aggregate([row["relevance"] for row in rows]),
        "citation_coverage": aggregate([1.0 if row["citation_count"] else 0.0 for row in rows]),
        "with_evidence": len(evidence_rows),
        "strategy": preset.name if preset else "系统默认",
        "role": role,
        "department_id": department_id,
    }
    return {
        "kind": "generation",
        "summary": summary,
        "rows": rows,
        "markdown": _markdown(summary, rows),
    }
