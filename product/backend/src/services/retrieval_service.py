"""检索服务，供 API 与 Celery 任务调用的检索门面。

把获取某知识库的检索管线、按构建块开关检索封装成函数，上层不必关心管线缓存与
知识库维度的隔离。

管线按知识库编号与分块策略缓存在检索模块内部，向量模型加载成本高，必须跨请求复用；
本模块只做转发与预热编排，不含任何落库逻辑。
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

    构建块开关默认全开；返回条数可覆盖，精排链路下等价于精排保留条数，不传取配置值。
    返回附带耗时与统计的响应，策略字段为实际生效的策略名。
    """
    pipeline = get_pipeline(kb_id)
    return pipeline.retrieve(query, strategies, top_k=top_k)


def pipeline_info(kb_id: int) -> dict[str, Any]:
    """返回某知识库检索管线的状态（模型加载情况、链路参数、默认开关）。"""
    return get_pipeline(kb_id).info()


def warmup_all(*, include_reranker: bool = True) -> dict[str, Any]:
    """预热全局精排模型与所有已缓存的检索管线，启动时调用。

    尚无缓存的管线不会被凭空创建，预热只在已有知识库管线时生效；
    未预热的知识库在其首次检索时再加载。返回各阶段耗时，供健康检查展示。
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
