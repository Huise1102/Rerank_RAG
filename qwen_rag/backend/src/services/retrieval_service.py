"""检索服务（retrieval_service.py）：供 API / Celery 任务调用的检索门面。

把「取某知识库的检索管线 → 按构建块开关检索」封装成函数，上层不必关心管线缓存与
kb_id 维度隔离：

    from src.services.retrieval_service import retrieve, warmup_all, pipeline_info

    resp = retrieve(kb_id, "问题文本", strategies={"vector": True, "bm25": True, ...})

管线按 ``(kb_id, 分块策略)`` 缓存在 ``retrieval.pipeline`` 内部（向量模型加载成本高，
必须跨请求复用），本模块只做转发与预热编排，不含任何落库逻辑。
"""

from __future__ import annotations

import time
from typing import Any

from src.core.config import settings
from src.retrieval.base import get_logger
from src.retrieval.pipeline import RetrievalResponse, get_pipeline

logger = get_logger(__name__)


def retrieve(
    kb_id: int,
    query: str,
    strategies: dict[str, bool] | None = None,
    top_k: int | None = None,
) -> RetrievalResponse:
    """在指定知识库上执行一次检索。

    :param kb_id: 知识库 id；:param query: 查询文本
    :param strategies: 构建块开关 ``{"vector","bm25","fusion","rerank"}``，默认全 true
        （只开 vector → 纯向量；只开 bm25 → 纯 BM25；vector+bm25(+fusion) → 混合；
        再加 rerank → 混合+精排）
    :param top_k: 覆盖最终返回条数；不传取配置值（``hybrid_rerank`` 下等价于精排保留条数）
    :returns: ``RetrievalResponse``，``strategy`` 为实际生效的策略名
    """
    pipeline = get_pipeline(kb_id)
    return pipeline.retrieve(query, strategies, top_k=top_k)


def pipeline_info(kb_id: int) -> dict[str, Any]:
    """返回某知识库检索管线的状态（模型加载情况、链路参数、默认开关）。"""
    return get_pipeline(kb_id).info()


def warmup_all(*, include_reranker: bool = True) -> dict[str, Any]:
    """预热全局精排模型与**所有已缓存**的检索管线（FastAPI 启动时调用）。

    尚无缓存的管线不会被凭空创建 —— 预热只在已有知识库管线时生效；未预热的知识库
    在其首次检索时再加载。返回各阶段耗时，供 /health 展示。
    """
    # 延迟导入，避免在未使用检索时也拉起依赖
    from src.retrieval import pipeline as pipeline_module
    from src.retrieval.reranker import get_reranker

    detail: dict[str, Any] = {}
    t0 = time.perf_counter()

    if include_reranker and settings.rerank.enabled:
        try:
            detail["reranker"] = round(get_reranker().load(), 3)
        except Exception as exc:  # noqa: BLE001 - 预热失败不应阻断服务启动
            detail["reranker_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("精排模型预热失败: %s", exc)

    for (kb_id, chunk_strategy), pipeline in list(pipeline_module._PIPELINES.items()):
        try:
            used = pipeline.warmup(include_reranker=False)
            detail[f"kb{kb_id}/{chunk_strategy}"] = used.get("total", 0.0)
        except Exception as exc:  # noqa: BLE001
            detail[f"kb{kb_id}/{chunk_strategy}_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("知识库 %s 管线预热失败: %s", kb_id, exc)

    detail["total"] = round(time.perf_counter() - t0, 3)
    logger.info("检索预热完成: %s", detail)
    return detail
