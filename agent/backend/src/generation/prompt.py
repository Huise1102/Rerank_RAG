"""提示组装，把检索到的文档块拼成 LLM 的输入。

系统提示包含三条约束：仅依据上下文回答，标注来源编号，上下文不足时明确说明。
引用格式要求只在动态追加的片段里写一遍，配置中的系统提示不重复。
上下文按分数降序填入字符预算，每条前带编号、来源与页码，编号是引用能映射回文档块的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from src.core.config import settings

# 引用标注风格对应的排版要求
_CITATION_INSTRUCTIONS: dict[str, str] = {
    "bracket": "回答时用 [编号] 标注每条结论的依据来源，例如：应在规定期限内提出申请[1]。",
    "inline": "回答时在句末用（来源：文件名 第N页）的形式标注依据。",
    "none": "无需标注来源。",
}


@dataclass
class PromptContext:
    """送入提示的单条上下文。编号是模型看到的编号，从 1 开始，
    引用解析依赖它映射回文档块。
    """

    index: int
    text: str
    source: str = ""
    page: int | str = ""
    score: float | None = None
    chunk_id: str = ""

    def header(self, *, include_source: bool = True, include_scores: bool = False) -> str:
        """生成编号、来源与页码这一行。"""
        if not include_source:
            return f"[{self.index}]"
        parts = [f"[{self.index}]"]
        parts.append(f"来源：{self.source or '未知'}")
        if self.page not in ("", None):
            parts.append(f"页码：{self.page}")
        if include_scores and self.score is not None:
            parts.append(f"相关度：{self.score:.4f}")
        return " ".join(parts)

    def render(self, *, include_source: bool = True, include_scores: bool = False) -> str:
        return f"{self.header(include_source=include_source, include_scores=include_scores)}\n{self.text.strip()}"


@dataclass
class BuiltPrompt:
    """组装好的消息列表及其元信息。"""

    messages: list[dict[str, Any]]
    contexts: list[PromptContext] = field(default_factory=list)
    used_chars: int = 0
    dropped: int = 0  # 因超出预算被丢弃的上下文条数
    has_image: bool = False  # 是否附带图片（图片问答时无上下文也允许调用模型）

    @property
    def context_block(self) -> str:
        """最后一条用户消息的文本部分，附图时该消息是内容块数组。"""
        content = self.messages[-1]["content"]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return str(block.get("text", ""))
            return ""
        return str(content)

    def summary(self) -> str:
        return (
            f"上下文 {len(self.contexts)} 条 / {self.used_chars} 字符"
            + (f"（丢弃 {self.dropped} 条超出预算）" if self.dropped else "")
        )



def contexts_from_results(results: Iterable[Any], *, start_index: int = 1) -> list[PromptContext]:
    """把检索结果或字典转成提示上下文，两种输入都兼容。"""
    contexts: list[PromptContext] = []
    for offset, item in enumerate(results):
        if isinstance(item, dict):
            meta = item.get("metadata", {}) or {}
            contexts.append(
                PromptContext(
                    index=start_index + offset,
                    text=str(item.get("text", "")),
                    source=str(item.get("source") or meta.get("source", "")),
                    page=item.get("page", meta.get("page", "")),
                    score=item.get("final_score", item.get("score")),
                    chunk_id=str(item.get("chunk_id", "")),
                )
            )
        else:
            meta = getattr(item, "metadata", {}) or {}
            score = getattr(item, "final_score", None)
            if score is None:
                score = getattr(item, "score", None)
            contexts.append(
                PromptContext(
                    index=start_index + offset,
                    text=str(getattr(item, "text", "")),
                    source=str(getattr(item, "source", "") or meta.get("source", "")),
                    page=meta.get("page", ""),
                    score=score,
                    chunk_id=str(getattr(item, "chunk_id", "")),
                )
            )
    return contexts


def select_within_budget(
    contexts: Sequence[PromptContext],
    max_chars: int | None = None,
) -> tuple[list[PromptContext], int]:
    """按字符预算裁剪上下文并保持原顺序，返回保留的上下文与被丢弃的条数。

    按顺序累加，超出预算即停止。单条超过预算一半时截断，长文档块开头通常是关键定义或条款。
    """
    budget = max_chars if max_chars is not None else settings.generation.context_max_chars
    kept: list[PromptContext] = []
    used = 0
    dropped = 0

    for ctx in contexts:
        cost = len(ctx.text) + len(ctx.header()) + 2
        if used + cost <= budget:
            kept.append(ctx)
            used += cost
            continue
        # 预算还有富余时，截断当前这条而不是直接放弃
        remaining = budget - used - len(ctx.header()) - 2
        if remaining >= 200:
            kept.append(
                PromptContext(
                    index=ctx.index, text=ctx.text[:remaining] + "…",
                    source=ctx.source, page=ctx.page, score=ctx.score, chunk_id=ctx.chunk_id,
                )
            )
            used = budget
        dropped += 1
        break

    return kept, dropped



class PromptBuilder:
    """把系统提示、上下文与问题组装成 OpenAI 格式的消息列表。"""
    def __init__(
        self,
        *,
        system_prompt: str | None = None,
        user_template: str | None = None,
        citation_style: str | None = None,
        include_source: bool | None = None,
        include_scores: bool | None = None,
        context_max_chars: int | None = None,
    ) -> None:
        cfg = settings.generation
        self.system_prompt = system_prompt or cfg.system_prompt
        self.user_template = user_template or cfg.user_prompt_template
        self.citation_style = (citation_style or cfg.citation_style).strip().lower()
        if self.citation_style not in _CITATION_INSTRUCTIONS:
            raise ValueError(
                f"未知引用风格 '{self.citation_style}'，可选：{' | '.join(_CITATION_INSTRUCTIONS)}"
            )
        self.include_source = cfg.include_source if include_source is None else include_source
        self.include_scores = cfg.include_scores if include_scores is None else include_scores
        self.context_max_chars = context_max_chars or cfg.context_max_chars
        # 没有可用上下文时的固定回复，不调用模型直接返回
        self.no_context_reply = cfg.no_context_reply
    def build_system_prompt(self) -> str:
        """在配置的系统提示后追加引用格式要求。"""
        extra = _CITATION_INSTRUCTIONS.get(self.citation_style, "")
        if self.citation_style == "none":
            return self.system_prompt
        return f"{self.system_prompt.rstrip()}\n{extra}"
    def render_context(self, contexts: Sequence[PromptContext]) -> str:
        """渲染上下文块，每条前带编号、来源与页码。"""
        return "\n\n".join(
            ctx.render(include_source=self.include_source, include_scores=self.include_scores)
            for ctx in contexts
        )
    def build(
        self,
        question: str,
        contexts: Sequence[PromptContext] | Sequence[Any],
        *,
        history: Sequence[tuple[str, str]] | None = None,
        image: str | None = None,
    ) -> BuiltPrompt:
        """组装完整输入。

        上下文可传提示上下文或检索结果列表、字典，本方法自行归一化；
        历史只保留最近若干轮；传入图片时最后一条用户消息是内容块数组，
        图片以图片链接块承载，消息可直接传给 OpenAI SDK。
        """
        items = list(contexts or [])
        if items and not isinstance(items[0], PromptContext):
            items = contexts_from_results(items)

        selected, dropped = select_within_budget(items, self.context_max_chars)

        if selected:
            block = self.render_context(selected)
            user_content = self.user_template.format(context=block, question=question.strip())
        else:
            # 无上下文时仍走资料不足的路径，不伪造上下文
            user_content = self.user_template.format(context="（无可用参考资料）", question=question.strip())

        system_text = self.build_system_prompt()
        if image:
            user_content = f"{user_content}\n\n{settings.generation.image_user_notice}"
            system_text = f"{system_text}\n{settings.generation.image_system_rule}"

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]

        if history:
            turns = max(settings.generation.history_max_turns, 0) * 2
            for role, content in list(history)[-turns:] if turns else []:
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        if image:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_content},
                        {"type": "image_url", "image_url": {"url": image}},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": user_content})

        used = sum(len(ctx.text) + len(ctx.header()) for ctx in selected)
        return BuiltPrompt(
            messages=messages, contexts=list(selected), used_chars=used, dropped=dropped,
            has_image=bool(image),
        )

    def __call__(self, question: str, contexts: Sequence[Any], **kwargs: Any) -> BuiltPrompt:
        return self.build(question, contexts, **kwargs)


def build_messages(
    question: str,
    contexts: Sequence[PromptContext] | Sequence[Any],
    *,
    history: Sequence[tuple[str, str]] | None = None,
) -> BuiltPrompt:
    """便捷函数，一次调用完成组装，自动判断输入类型。"""
    items = list(contexts)
    if items and not isinstance(items[0], PromptContext):
        items = contexts_from_results(items)
    return PromptBuilder().build(question, items, history=history)
