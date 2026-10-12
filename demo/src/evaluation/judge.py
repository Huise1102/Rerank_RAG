"""LLM 裁判（judge.py）：用 DeepSeek 对答案打分，替代 RAGAS（后者依赖重且有 pydantic 兼容风险）。

自建的好处是可控可核查：维度、提示词、量程由本项目决定，裁判原文与理由一并存档，答辩时
能当场解释"为什么给 3 分"。

两个维度：``faithfulness``（1~5，论断能否在参考资料中找到依据，重点抓编造）、
``answer_relevancy``（1~5，是否切题；与忠实度独立）。

可靠性保障：``judge_temperature=0`` 降低随机性；强制 JSON，解析失败重试一次（仍失败则记该
维度缺失而非猜分）；越界分数丢弃且不计入均值；聚合时分母只算成功评分的条数。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Sequence

from config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 支持的评分维度及其中文名（用于报告表头）
METRIC_LABELS: dict[str, str] = {
    "faithfulness": "忠实度",
    "answer_relevancy": "答案相关性",
}


_JUDGE_SYSTEM = """你是一位严格、公正的检索增强问答（RAG）质量评审员。
你的任务是对"候选人给出的答案"打分。

评分要求：
1. 只依据提供的【参考资料】判断，不要引入你自己的知识。
2. 严格按照给定的评分标准打分，不要因为答案文笔好而给高分。
3. 必须先给出理由，再给分数；理由要具体引用参考资料中的内容或指出缺失。
4. 只输出 JSON，不要输出任何其它文字或代码块标记。

评分标准（1~5 整数）：
- faithfulness（忠实度）：答案中的论断是否都能在参考资料中找到依据。
  5 = 每个论断都有明确依据，无任何编造；
  4 = 基本有依据，个别措辞略有外推；
  3 = 主要论断有依据，但存在少量未在资料中出现的信息；
  2 = 有较明显的内容是资料中没有的（编造或臆测）；
  1 = 大量内容与资料无关或凭空捏造。
- answer_relevancy（答案相关性）：答案是否切题、是否真正回答了问题。
  5 = 完全切题，直接、完整地回答了问题；
  4 = 基本切题，个别部分偏题或不够完整；
  3 = 部分切题，回避了问题的关键部分；
  2 = 明显偏题或只泛泛而谈；
  1 = 完全答非所问，或只是复述资料。

注意：若答案明确说明"根据现有资料无法回答"，而你核对后认为资料确实不足以回答，
这属于**正确行为**，faithfulness 应给 5 分，answer_relevancy 给 4~5 分。"""

_JUDGE_USER = """【问题】
{question}

【参考资料】
{context}

【候选人答案】
{answer}

请严格按以下 JSON 格式输出（分数必须是 1~5 的整数）：
{{
  "faithfulness": {{"score": <1-5>, "reason": "<简要理由，50 字内>"}},
  "answer_relevancy": {{"score": <1-5>, "reason": "<简要理由，50 字内>"}}
}}"""



@dataclass
class JudgeScore:
    """一次裁判的评分结果。"""

    question: str = ""
    strategy: str = ""
    category: str = ""
    #: 维度 -> 分数（1~5）
    scores: dict[str, float] = field(default_factory=dict)
    #: 维度 -> 裁判给出的理由
    reasons: dict[str, str] = field(default_factory=dict)
    #: 裁判原始输出（排查裁判跑偏时用）
    raw: str = ""
    error: str = ""
    seconds: float = 0.0
    tokens: int = 0

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.scores)

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "strategy": self.strategy,
            "category": self.category,
            "question": self.question,
            "seconds": round(self.seconds, 2),
            "tokens": self.tokens,
            "error": self.error,
        }
        for m in settings.evaluation.judge_metrics:
            row[f"{m}_score"] = self.scores.get(m)
            row[f"{m}_reason"] = self.reasons.get(m, "")
        return row


def _parse_judge_json(text: str, metrics: Sequence[str]) -> dict[str, dict[str, Any]]:
    """宽容解析裁判输出：容忍 ```json 代码块与前后说明文字，而不是直接判失败。"""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    # 截取第一个 { 到最后一个 }（应对前后多余文字）
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 退化路径：用正则直接抠分数
        out: dict[str, dict[str, Any]] = {}
        for m in metrics:
            match = re.search(rf'"{m}"\s*:\s*\{{[^}}]*?"score"\s*:\s*(\d+)', cleaned, re.S)
            if match:
                out[m] = {"score": int(match.group(1)), "reason": "(JSON 解析失败，仅提取到分数)"}
        if out:
            logger.warning("裁判输出 JSON 解析失败，已用正则兜底提取分数")
        return out

    out = {}
    for m in metrics:
        block = data.get(m)
        if isinstance(block, dict):
            out[m] = block
        elif isinstance(block, (int, float)):
            out[m] = {"score": block, "reason": ""}
    return out



class LLMJudge:
    """
    基于 DeepSeek 的答案质量裁判。

    ``judge.score(question, contexts, answer, strategy=..., category=...)`` → ``JudgeScore``。
    """

    def __init__(
        self,
        *,
        client: Any = None,
        metrics: Sequence[str] | None = None,
        model: str | None = None,
    ) -> None:
        self.metrics = list(metrics or settings.evaluation.judge_metrics)
        unknown = [m for m in self.metrics if m not in METRIC_LABELS]
        if unknown:
            raise ValueError(
                f"未知评分维度 {unknown}，可选：{list(METRIC_LABELS)}"
            )
        self.model = model or settings.evaluation.judge_model
        self._client = client  # 可注入 mock 客户端，测试时零 token

    @property
    def client(self):
        if self._client is None:
            from src.generation.llm import get_llm_client

            self._client = get_llm_client()
        return self._client

    @staticmethod
    def format_context(chunks: Sequence[Any], max_chars: int = 20000) -> str:
        """
        把检索片段渲染成裁判可读的参考资料块，编号必须与生成阶段一致。

        必须传入生成阶段**实际使用**的上下文（``LLMResponse.contexts``）而非检索到的
        全部结果：生成阶段会按 ``context_max_chars`` 预算裁剪，若裁判拿到未裁剪的列表，
        编号体系就与模型看到的不同，会把正常引用误判为"引用了不存在的编号"（假阳性幻觉）。
        ``max_chars`` 默认远大于生成预算（6000），传进来的既已裁剪过，这里只作防御性兜底。
        """
        lines: list[str] = []
        used = 0
        for i, item in enumerate(chunks, 1):
            if isinstance(item, dict):
                text = str(item.get("text", ""))
                source = item.get("source", "")
                page = item.get("page", "")
            else:
                text = str(getattr(item, "text", ""))
                source = getattr(item, "source", "")
                page = getattr(item, "page", "")
            header = f"[{i}] 来源：{source} 页码：{page}"
            block = f"{header}\n{text.strip()}"
            if used + len(block) > max_chars:
                logger.warning(
                    "裁判参考资料被截断：第 %d 条起未展示（预算 %d 字）。"
                    "请确认传入的是生成阶段实际使用的上下文。", i, max_chars,
                )
                break
            lines.append(block)
            used += len(block)
        return "\n\n".join(lines) if lines else "（无参考资料）"

    def score(
        self,
        question: str,
        contexts: Sequence[Any],
        answer: str,
        *,
        strategy: str = "",
        category: str = "",
        retries: int = 1,
    ) -> JudgeScore:
        """
        对一条答案打分。

        :param contexts: 送入生成阶段的检索片段
        :param retries: JSON 解析失败时的重试次数
        :returns: JudgeScore（含分数、理由、原始输出）
        """
        import time

        result = JudgeScore(question=question, strategy=strategy, category=category)
        if not answer or not answer.strip():
            result.error = "答案为空，无法评分"
            return result

        prompt = _JUDGE_USER.format(
            question=question.strip(),
            context=self.format_context(contexts),
            answer=answer.strip(),
        )
        messages = [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ]

        t0 = time.perf_counter()
        last_error = ""
        for attempt in range(retries + 1):
            try:
                completion = self.client.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=settings.evaluation.judge_temperature,
                    max_tokens=settings.evaluation.judge_max_tokens,
                    stream=False,
                )
                raw = (completion.choices[0].message.content or "").strip()
                result.raw = raw
                if getattr(completion, "usage", None) is not None:
                    result.tokens += int(getattr(completion.usage, "total_tokens", 0) or 0)

                parsed = _parse_judge_json(raw, self.metrics)
                if not parsed:
                    last_error = "裁判输出中未解析到任何分数"
                    if attempt < retries:
                        # 追加一句提醒后重试，比原样重发更可能成功
                        messages = messages + [
                            {"role": "assistant", "content": raw[:500]},
                            {"role": "user", "content": "请只输出规定的 JSON，不要任何其它文字。"},
                        ]
                        continue
                    break

                scale = settings.evaluation.judge_scale
                for metric, block in parsed.items():
                    try:
                        value = float(block.get("score"))
                    except (TypeError, ValueError):
                        continue
                    # 越界分数判为裁判异常，避免污染均值
                    if not (1 <= value <= scale):
                        logger.warning("裁判给出越界分数 %s=%s，已忽略该维度", metric, value)
                        continue
                    result.scores[metric] = value
                    result.reasons[metric] = str(block.get("reason", "")).strip()
                break
            except Exception as exc:  # noqa: BLE001 - 裁判失败不应让整轮评估崩掉
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= retries:
                    break

        result.seconds = time.perf_counter() - t0
        if not result.scores:
            result.error = last_error or "裁判未返回可用分数"
        return result

    def score_batch(
        self,
        cases: Sequence[dict[str, Any]],
        *,
        show_progress: bool = True,
        workers: int | None = None,
    ) -> list[JudgeScore]:
        """
        批量打分（并发）。

        裁判调用是网络等待型，并发数由 ``judge_workers`` 控制，4 个并发即可把总耗时
        压到约 1/4。

        :param cases: ``[{question, contexts, answer, strategy, category}, ...]``
        """
        items = list(cases)
        if not items:
            return []
        n_workers = workers or settings.evaluation.judge_workers
        n_workers = max(1, min(n_workers, len(items)))

        def _one(case: dict[str, Any]) -> JudgeScore:
            return self.score(
                case.get("question", ""),
                case.get("contexts") or [],
                case.get("answer", ""),
                strategy=case.get("strategy", ""),
                category=case.get("category", ""),
            )

        if show_progress:
            from tqdm import tqdm

            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                return list(tqdm(pool.map(_one, items), total=len(items),
                                 desc="LLM 裁判评分", unit="条", ncols=92))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            return list(pool.map(_one, items))



def aggregate_scores(scores: Sequence[JudgeScore], metrics: Sequence[str] | None = None) -> dict[str, Any]:
    """
    汇总平均分。

    分母只算成功评分的条数：某维度某条缺失时不计 0 分，否则会把"裁判失败"当成"答案很差"。
    """
    ms = list(metrics or settings.evaluation.judge_metrics)
    out: dict[str, Any] = {"n": len(scores), "n_scored": sum(1 for s in scores if s.ok)}
    for m in ms:
        values = [s.scores[m] for s in scores if m in s.scores]
        out[m] = round(sum(values) / len(values), 3) if values else None
        out[f"{m}_n"] = len(values)
    out["avg_seconds"] = round(
        sum(s.seconds for s in scores) / max(len(scores), 1), 2
    )
    out["total_tokens"] = sum(s.tokens for s in scores)
    out["errors"] = sum(1 for s in scores if s.error)
    return out
