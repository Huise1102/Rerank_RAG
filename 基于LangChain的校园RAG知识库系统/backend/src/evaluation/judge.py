import asyncio
import json
import re

from src.generation.llm import build_chat_model

JUDGE_PROMPT = """你是校园制度问答评估员。请根据给定参考资料判断回答质量。
只输出 JSON，不要输出解释性文字，格式为：
{{"faithfulness": 0.0, "relevance": 0.0, "reason": "简要说明"}}

问题：{question}
参考资料：
{context}
回答：
{answer}
"""

JSON_RE = re.compile(r"\{.*\}", re.S)


def _parse(text: str) -> dict:
    match = JSON_RE.search(text or "")
    if not match:
        raise ValueError("裁判未返回 JSON")
    data = json.loads(match.group(0))
    faithfulness = float(data.get("faithfulness", 0.0))
    relevance = float(data.get("relevance", 0.0))
    return {
        "faithfulness": max(0.0, min(1.0, faithfulness)),
        "relevance": max(0.0, min(1.0, relevance)),
        "reason": str(data.get("reason", ""))[:500],
    }


async def judge_answer(
    question: str,
    answer: str,
    contexts: list[str],
    model_config: dict,
    retries: int = 1,
) -> dict:
    context_text = "\n\n".join(contexts)[:4000] or "无参考资料"
    prompt = JUDGE_PROMPT.format(question=question, context=context_text, answer=answer)
    last_error = ""
    for attempt in range(retries + 1):
        try:
            model = build_chat_model(model_config)
            response = await model.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            return _parse(str(content))
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                await asyncio.sleep(1)
    return {"faithfulness": 0.0, "relevance": 0.0, "reason": f"裁判失败：{last_error[:300]}"}
