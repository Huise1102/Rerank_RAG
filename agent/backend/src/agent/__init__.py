"""Agent 调度层，由 LLM 逐轮驱动检索决策。

对外提供 AgentLoop（ReAct 循环）、AgentResult（一次问答结果）
与 MAX_DECISION_ROUNDS（单问题最多决策轮数）。
"""

from __future__ import annotations

from src.agent.loop import MAX_DECISION_ROUNDS, AgentLoop, AgentResult

__all__ = ["AgentLoop", "AgentResult", "MAX_DECISION_ROUNDS"]