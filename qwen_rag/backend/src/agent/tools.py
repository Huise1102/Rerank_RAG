"""Agent 工具层（tools.py）：薄封装，复用检索服务，不含任何检索/生成业务。

每个工具只做「参数校验 + 调复用层 + 回填轨迹」。策略名 → 现有四开关的映射是唯一新增逻辑。
"""

from __future__ import annotations

import time
from typing import Any

from src.agent.state import AgentState
from src.generation.prompt import contexts_from_results
from src.retrieval.base import RetrievalResult

#: 策略名 → retrieval_service 四开关。第三版由 Agent 逐轮选择，而非知识库固定配置。
STRATEGY_SWITCHES: dict[str, dict[str, bool]] = {
    "vector": {"vector": True, "bm25": False, "fusion": False, "rerank": False},
    "bm25": {"vector": False, "bm25": True, "fusion": False, "rerank": False},
    "hybrid": {"vector": True, "bm25": True, "fusion": True, "rerank": False},
    "hybrid_rerank": {"vector": True, "bm25": True, "fusion": True, "rerank": True},
}


def strategy_to_switches(name: str) -> dict[str, bool]:
    if name not in STRATEGY_SWITCHES:
        raise ValueError(f"未知检索策略 '{name}'，可选：{sorted(STRATEGY_SWITCHES)}")
    return dict(STRATEGY_SWITCHES[name])


def _doc_to_client(r: RetrievalResult) -> dict[str, Any]:
    """检索结果转前端扁平字段（与 chat_service._doc_to_client 同构）。"""
    return {
        "chunk_id": r.chunk_id,
        "rank": r.rank,
        "text": r.text,
        "score": r.final_score,
        "fused_score": r.fused_score,
        "rerank_score": r.rerank_score,
        "matched_by": list(r.matched_by),
        "source": r.source,
        "page": r.page,
    }


def execute_retrieve(state: AgentState, query: str, strategy: str) -> dict[str, Any]:
    """执行一次检索并回填证据；返回本轮命中的可渲染结果与轨迹元数据。"""
    from src.services.retrieval_service import retrieve

    t0 = time.perf_counter()
    resp = retrieve(state.kb_id, query, strategy_to_switches(strategy))
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    new = state.add_evidences(resp.results)
    # 跨轮连续编号：本轮新增证据的起始编号 = 已累积条数 - 新增条数 + 1
    start_index = len(state.evidences) - len(new) + 1
    if new:
        rendered = "\n\n".join(
            c.render() for c in contexts_from_results(new, start_index=start_index)
        )
    else:
        rendered = "（本轮检索未命中任何文档块）"

    return {
        "query": query,
        "strategy": resp.strategy,
        "result_count": len(resp.results),
        "new_count": len(new),
        "rendered": rendered,
        "docs": [_doc_to_client(r) for r in new],
        "stats": resp.stats,
        "elapsed_ms": elapsed_ms,
    }