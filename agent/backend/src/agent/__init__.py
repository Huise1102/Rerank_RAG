"""Agentic 调度层：LLM 逐轮驱动检索决策（第三版）。

对外暴露 :class:`AgentLoop`（ReAct 循环）、:class:`AgentResult`（一次问答结果）与
``MAX_DECISION_ROUNDS``（硬约束：单问题最多决策轮数）。
"""

from __future__ import annotations

from src.agent.loop import MAX_DECISION_ROUNDS, AgentLoop, AgentResult

__all__ = ["AgentLoop", "AgentResult", "MAX_DECISION_ROUNDS"]