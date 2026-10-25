"""提示词组装（prompt.py）：把检索到的文档块拼成 LLM 的 prompt。

系统提示词要写三条约束：仅依据上下文回答（否则模型用参数里的常识作答）、标注来源编号
[1][2]（有引用标注时幻觉率显著下降）、上下文不足时明确说明。引用格式要求只在动态追加
片段里写一遍，config 的 system_prompt 里不重复。上下文按分数降序填到字符预算，每条前带
``[1] 来源：测试.pdf 页码：18`` —— 编号是 [1] 能解析回文档块的前提。

（从 demo/src/generation/prompt.py 逐字复制，仅把 config 导入改为 src.core.config。）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from src.core.config import settings

#: 引用标注风格 -> 给模型的排版要求
_CITATION_INSTRUCTIONS: dict[str, str] = {
    "bracket": "回答时用 [编号] 标注每条结论的依据来源，例如：应在规定期限内提出申请[1]。",
    "inline": "回答时在句末用（来源：文件名 第N页）的形式标注依据。",
    "none": "无需标注来源。",
}


@dataclass
class PromptContext:
    """送入 prompt 的单条上下文。``index`` 是模型看到的编号（从 1 开始），citation.py
    依赖它把 [n] 映射回文档块。
    """

    index: int
    text: str
    source: str = ""
    page: int | str = ""
    score: float | None = None
    chunk_id: str = ""

    def header(self, *, include_source: bool = True, include_scores: bool = False) -> str:
        """生成 ``[1] 来源：xxx 页码：xxx`` 这一行。"""
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
    """组装好的 prompt（消息列表）及其元信息。"""

    messages: list[dict[str, Any]]
    contexts: list[PromptContext] = field(default_factory=list)
    used_chars: int = 0
    dropped: int = 0  # 因超出预算被丢弃的上下文条数
    has_image: bool = False  # 是否附带图片（图片问答时无上下文也允许调用模型）

    @property
    def context_block(self) -> str:
        """最后一条 user 消息的文本部分（附图时该消息是内容块数组）。"""
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
    """把检索结果（``RetrievalResult`` 或字典）转成 ``PromptContext``，两种输入都兼容。"""
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
    """按字符预算裁剪上下文（保持原顺序，即分数从高到低）。

    按顺序累加、超预算即停，而不是跳过超长的那条继续塞后面的：结果已按相关性降序，
    为塞一条低相关短文本而丢掉高相关长文本得不偿失。单条若超过预算一半，截断而不是
    整条丢弃（长文档块的开头通常就是最关键的定义或条款）。
    :returns: (保留的上下文, 被丢弃的条数)
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
    """把系统提示词 + 上下文 + 问题组装成 OpenAI 格式的消息列表。"""

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
        #: 没有可用上下文时的固定回复（不调用 LLM，直接返回）
        self.no_context_reply = cfg.no_context_reply
    def build_system_prompt(self) -> str:
        """在配置的系统提示词后追加引用格式要求（排版约定与安全底线分开，便于按场景切换）。"""
        extra = _CITATION_INSTRUCTIONS.get(self.citation_style, "")
        if self.citation_style == "none":
            return self.system_prompt
        return f"{self.system_prompt.rstrip()}\n{extra}"
    def render_context(self, contexts: Sequence[PromptContext]) -> str:
        """渲染上下文块，每条前带 ``[编号] 来源：xxx 页码：xxx``。"""
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
        """组装完整 prompt。

        :param contexts: ``PromptContext`` 或检索结果列表/字典均可（本方法自行归一化）
        :param history: 多轮对话历史 ``[(role, content), ...]``，只保留最近若干轮
        :param image: 图片 data URL（``data:image/png;base64,...``）；传入后最后一条
            user 消息改为内容块数组，图片以 ``image_url`` 块承载
        :returns: BuiltPrompt，``messages`` 可直接喂给 OpenAI SDK
        """
        # 允许直接传检索结果，否则调用方会拿到 AttributeError: no attribute 'header'
        items = list(contexts or [])
        if items and not isinstance(items[0], PromptContext):
            items = contexts_from_results(items)

        selected, dropped = select_within_budget(items, self.context_max_chars)

        if selected:
            block = self.render_context(selected)
            user_content = self.user_template.format(context=block, question=question.strip())
        else:
            # 无上下文：仍然走"不足则说明"的路径，而不是伪造上下文
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
    """便捷函数：一条语句完成组装（自动判断是 PromptContext 还是检索结果）。"""
    items = list(contexts)
    if items and not isinstance(items[0], PromptContext):
        items = contexts_from_results(items)
    return PromptBuilder().build(question, items, history=history)
