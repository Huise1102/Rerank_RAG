"""检索器共用基础设施。

RetrievalResult 把向量检索与 BM25 检索的结果归一到同一数据结构，分块 ID 作为跨索引主键。BaseRetriever 统一检索、批量检索与名称接口，子类只实现单次检索；索引在首次检索时加载，也可提前预热，并记录各步耗时。
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from config import settings



@dataclass
class RetrievalResult:
    """
    单条检索结果。

    retriever 与 rank 记录该结果来自哪一路检索器以及在本路中的名次。
    """

    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    retriever: str = ""
    rank: int = 0  # 本路名次，从 1 开始

    # 融合与精排过程的附加字段
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
        """对外展示的最终分数，优先取精排分数，其次融合分数，最后单路分数。"""
        if self.rerank_score is not None:
            return self.rerank_score
        if self.fused_score is not None:
            return self.fused_score
        return self.score

    def citation(self) -> str:
        """生成来源与页码的引用文字。"""
        page = self.metadata.get("page")
        return f"{self.source} 第 {page} 页" if page else self.source

    def to_dict(self) -> dict[str, Any]:
        """转为普通字典，供评估结果与接口响应使用。"""
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

    子类实现单次检索并返回结果列表，名次由基类统一补齐。
    """

    # 检索器名称，会写入结果对象
    name: str = "base"

    def __init__(self, strategy: str | None = None) -> None:
        self.strategy = (strategy or settings.chunk.default_strategy).strip().lower()
        self._loaded = False
        self._load_lock = threading.RLock()
        self._load_seconds: float = 0.0
        self.last_seconds: float = 0.0  # 最近一次检索耗时
    def warmup(self) -> float:
        """显式预热，加载索引或模型并返回耗时秒数。

        预热线程与首个查询可能同时进入：加锁并二次检查，保证只真正加载一次，后来者等待同一次加载完成。
        """
        t0 = time.perf_counter()
        with self._load_lock:
            if not self._loaded:
                self._load()
                self._loaded = True
        self._load_seconds = time.perf_counter() - t0
        return self._load_seconds

    @abstractmethod
    def _load(self) -> None:
        """加载索引或模型，由子类实现。"""

    @property
    @abstractmethod
    def size(self) -> int:
        """索引中的文档块总数。"""
    @abstractmethod
    def _do_retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        """执行检索，返回带名次的结果列表。"""

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievalResult]:
        """
        检索单条查询。

        查询文本为空时返回空列表，返回条数默认取该检索器的配置值。
        """
        if not query or not query.strip():
            return []
        if not self._loaded:
            self.warmup()

        k = top_k or self.default_top_k
        t0 = time.perf_counter()
        results = self._do_retrieve(query.strip(), k)
        self.last_seconds = time.perf_counter() - t0

        # 统一补全名次与来源标记，子类无需重复处理
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
        """批量检索，子类可覆盖以启用批处理推理。"""
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
    分步耗时记录器，逐步记录各阶段耗时而非只统计总耗时。
    """

    steps: list[tuple[str, float]] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)

    def record(self, label: str, seconds: float) -> None:
        self.steps.append((label, round(seconds, 4)))

    def measure(self, label: str):
        """返回上下文管理器，用于包裹需要计时的代码块。"""
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
    把分数线性归一化到 0 到 1。

    加权融合时向量相似度与 BM25 分数数量级差异较大，不归一化会让 BM25 单方面决定结果。所有分数相等时统一返回 1。
    """
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-12:
        return [1.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def get_logger(name: str):
    """
    获取标准库日志器。

    仅在根日志器尚未配置处理器时补充一个默认输出，避免导入阶段改动全局日志设置。
    """
    import logging

    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        # 未配置根日志器时补一个可用输出，便于观察耗时
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                               datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(settings.log.level)
    return logger
