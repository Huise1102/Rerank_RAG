"""阶段 2 —— 检索器共用基础设施（base.py）。

``RetrievalResult`` 把 Chroma 与 pickle 两路结果归一到同一 dataclass，``chunk_id`` 为跨索引主键
（两个索引的 chunk_id 已校验一致），融合层只需关心「文档块 + 分数 + 来源」。

``BaseRetriever`` 统一 ``retrieve`` / ``retrieve_batch`` / ``name`` 接口（子类只实现 ``_do_retrieve``），
加载惰性化（首次 ``retrieve`` 才读磁盘，``warmup()`` 可提前预热），各步耗时记录供评估与调试面板使用。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from config import settings



@dataclass
class RetrievalResult:
    """
    单条检索结果。

    ``retriever`` 与 ``rank`` 用于追踪「某条结果来自哪一路、原本排第几」。
    """

    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    retriever: str = ""
    rank: int = 0  # **本路**名次，从 1 开始

    # —— 融合/精排过程的可解释字段 ——
    fused_score: float | None = None  # 融合分数
    rerank_score: float | None = None  # 精排分数
    matched_by: list[str] = field(default_factory=list)  # 召回该块的检索器

    @property
    def source(self) -> str:
        return str(self.metadata.get("source", ""))

    @property
    def page(self) -> int | str:
        return self.metadata.get("page", "")

    @property
    def final_score(self) -> float:
        """对外展示的最终分数：精排 > 融合 > 单路。"""
        if self.rerank_score is not None:
            return self.rerank_score
        if self.fused_score is not None:
            return self.fused_score
        return self.score

    def citation(self) -> str:
        """生成引用标注，如 ``[1] 测试.pdf 第 12 页``。"""
        page = self.metadata.get("page")
        return f"{self.source} 第 {page} 页" if page else self.source

    def to_dict(self) -> dict[str, Any]:
        """转为普通字典（评估结果与 JSON 响应）。"""
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "score": self.score,
            "final_score": self.final_score,
            "fused_score": self.fused_score,
            "rerank_score": self.rerank_score,
            "retriever": self.retriever,
            "rank": self.rank,
            "matched_by": list(self.matched_by),
            "metadata": dict(self.metadata),
        }

    def preview(self, width: int = 70) -> str:
        text = self.text.replace("\n", " ")
        return text[:width] + ("..." if len(text) > width else "")



class BaseRetriever(ABC):
    """
    检索器接口。

    子类实现 ``_do_retrieve`` 并返回 ``RetrievalResult``（``rank`` 由基类统一补为 1 起）。
    """

    #: 检索器名称，会写入 RetrievalResult.retriever
    name: str = "base"

    def __init__(self, strategy: str | None = None) -> None:
        self.strategy = (strategy or settings.chunk.default_strategy).strip().lower()
        self._loaded = False
        self._load_seconds: float = 0.0
        self.last_seconds: float = 0.0  # 最近一次检索耗时
    def warmup(self) -> float:
        """显式预热（加载索引/模型），返回耗时秒数。"""
        t0 = time.perf_counter()
        if not self._loaded:
            self._load()
            self._loaded = True
        self._load_seconds = time.perf_counter() - t0
        return self._load_seconds

    @abstractmethod
    def _load(self) -> None:
        """加载索引或模型（子类实现）。"""

    @property
    @abstractmethod
    def size(self) -> int:
        """索引中的文档块总数。"""
    @abstractmethod
    def _do_retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        """执行检索，返回已带 rank 的结果列表。"""

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievalResult]:
        """
        检索单条查询。

        :param query: 查询文本
        :param top_k: 返回条数，默认取该检索器的配置值
        """
        if not query or not query.strip():
            return []
        if not self._loaded:
            self.warmup()

        k = top_k or self.default_top_k
        t0 = time.perf_counter()
        results = self._do_retrieve(query.strip(), k)
        self.last_seconds = time.perf_counter() - t0

        # 统一补全 rank 与来源标记，子类无需重复处理
        for i, item in enumerate(results, 1):
            item.rank = i
            item.retriever = self.name
            if self.name not in item.matched_by:
                item.matched_by.append(self.name)
        return results

    def retrieve_batch(
        self,
        queries: Sequence[str],
        top_k: int | None = None,
        show_progress: bool = False,
    ) -> list[list[RetrievalResult]]:
        """批量检索；子类可覆盖以启用真正的批处理（如向量化批推理）。"""
        iterator: Iterable[str] = queries
        if show_progress:
            from tqdm import tqdm

            iterator = tqdm(queries, desc=f"检索[{self.name}]", unit="问", ncols=90)
        return [self.retrieve(q, top_k) for q in iterator]

    @property
    def default_top_k(self) -> int:
        """该检索器的默认返回条数。"""
        return settings.retrieval.top_k

    def info(self) -> dict[str, Any]:
        """检索器状态，供日志与调试面板使用。"""
        return {
            "name": self.name,
            "strategy": self.strategy,
            "loaded": self._loaded,
            "size": self.size if self._loaded else None,
            "load_seconds": round(self._load_seconds, 3),
            "last_seconds": round(self.last_seconds, 3),
        }



@dataclass
class TimingRecorder:
    """
    分步耗时记录器：阶段 5 要对比各策略延迟，因此每步都记，而不是只统计总耗时。
    """

    steps: list[tuple[str, float]] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)

    def record(self, label: str, seconds: float) -> None:
        self.steps.append((label, round(seconds, 4)))

    def measure(self, label: str):
        """上下文管理器：``with timing.measure("vector"): ...``"""
        return _Timer(self, label)

    @property
    def total(self) -> float:
        return round(time.perf_counter() - self._t0, 4)

    def as_dict(self) -> dict[str, float]:
        out = {label: seconds for label, seconds in self.steps}
        out["total"] = self.total
        return out

    def summary(self) -> str:
        parts = [f"{label}={seconds * 1000:.0f}ms" for label, seconds in self.steps]
        return " ".join(parts) + f" | 合计={self.total * 1000:.0f}ms"


class _Timer:
    def __init__(self, recorder: TimingRecorder, label: str) -> None:
        self.recorder = recorder
        self.label = label

    def __enter__(self) -> "_Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.recorder.record(self.label, time.perf_counter() - self._start)



def minmax_normalize(scores: Sequence[float]) -> list[float]:
    """
    把分数线性归一化到 [0, 1]。

    用于 ``fusion="weighted"``：向量相似度约 0.3~0.9、BM25 可达 20+，量纲不同，
    不归一化直接加权会让 BM25 单方面决定结果。全部相等时返回 1.0（权重平均分配）。
    """
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-12:
        return [1.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def get_logger(name: str):
    """
    获取标准库 logger；仅在根日志器未配置 handler 时补一个默认输出，
    避免模块导入阶段就改动全局日志设置。
    """
    import logging

    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        # 未配置根日志器时给个可用输出，便于开发期观察耗时
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                               datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(settings.log.level)
    return logger
