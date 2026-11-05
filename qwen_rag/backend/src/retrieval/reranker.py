"""交叉编码器精排（reranker.py）。

用 ``BAAI/bge-reranker-v2-m3`` 对混合检索的候选做精排，返回 Top-k。

召回用的双编码器把 query 与文档分别编码，两者从未交互，只能判断"大致相关"；
交叉编码器把 (query, doc) 拼接后整体送入模型做词级交互，准确得多但计算量巨大，
无法对全库实时打分，因此必须"先召回（几十条）再精排"。

分数是 logits，可为负、不是 0~1 概率，排序只看相对大小，故默认 ``normalize_score=False``；
需要展示"相关度百分比"或用 ``score_threshold`` 卡阈值时打开它（sigmoid 映射到 0~1）。
不同 query 之间的分数不可直接比较，评估时只能用于排序。

``config.rerank.backend``：``cross_encoder``（默认，CrossEncoder，无需 FlagEmbedding 约 1.5 GB 依赖）、
``flag_embedding``（FlagReranker，与官方示例逐一对应，保留供答辩时交叉验证）。

（从 demo/src/retrieval/reranker.py 逐字复制，仅把 config 导入改为 src.core.config。）
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Sequence

from src.core.config import settings
from src.core.model_lock import MODEL_LOAD_LOCK
from src.retrieval.base import RetrievalResult, get_logger

logger = get_logger(__name__)


def resolve_local_reranker(model_name: str) -> Path | None:
    """在本地 HuggingFace 缓存中查找 reranker 模型（绝不触发网络请求）。"""
    path = Path(model_name)
    if path.exists():
        return path
    try:
        from huggingface_hub import snapshot_download

        return Path(snapshot_download(model_name, local_files_only=True))
    except Exception:  # noqa: BLE001
        return None


def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid。"""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class Reranker:
    """
    交叉编码器精排器（惰性加载，可跨查询复用）。
    """

    name = "rerank"

    def __init__(
        self,
        *,
        model_name: str | None = None,
        device: str | None = None,
        backend: str | None = None,
        normalize_score: bool | None = None,
    ) -> None:
        cfg = settings.rerank
        self.model_name = model_name or cfg.resolved_model
        self.backend = (backend or cfg.backend).strip().lower()
        if self.backend not in ("cross_encoder", "flag_embedding"):
            raise ValueError(f"未知 rerank 后端 '{self.backend}'，可选：cross_encoder | flag_embedding")

        self._device_pref = device or cfg.device
        self.normalize_score = cfg.normalize_score if normalize_score is None else normalize_score
        self._model: Any = None
        self._device = "unknown"
        self._loaded = False
        self.load_seconds: float = 0.0
        self.last_seconds: float = 0.0
    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> float:
        """加载模型，返回耗时秒数（幂等）。"""
        if self._loaded:
            return 0.0
        t0 = time.perf_counter()
        cfg = settings.rerank
        device = self._pick_device()

        if self.backend == "flag_embedding":
            from FlagEmbedding import FlagReranker

            # 与 embedding 模型等其他大模型的加载互斥，见 core/model_lock.py
            with MODEL_LOAD_LOCK:
                self._model = FlagReranker(
                    self.model_name,
                    use_fp16=(device == "cuda" and cfg.use_fp16),
                )
        else:
            from sentence_transformers import CrossEncoder

            ref = self._resolve_ref()
            with MODEL_LOAD_LOCK:
                self._model = CrossEncoder(ref, device=device, max_length=cfg.max_length)

        self._device = device
        self._loaded = True
        self.load_seconds = time.perf_counter() - t0
        logger.info(
            "Reranker 就绪: 模型=%s 后端=%s device=%s 加载耗时=%.1fs",
            self.model_name, self.backend, device, self.load_seconds,
        )
        return self.load_seconds

    def _pick_device(self) -> str:
        if self._device_pref and self._device_pref != "auto":
            return self._device_pref
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            return "cpu"

    def _resolve_ref(self) -> str:
        """
        解析模型引用：优先本地快照（离线可用），否则用仓库名。

        模型未下全时给出明确提示，而不是让 CrossEncoder 抛出难以理解的 safetensors 解析错误。
        """
        local = resolve_local_reranker(self.model_name)
        if local is not None:
            # 下载中断会留下不完整的 .incomplete 文件，需确认权重确实存在
            weights = list(local.glob("*.safetensors")) or list(local.glob("pytorch_model*.bin"))
            if not weights:
                raise RuntimeError(
                    f"本地模型目录 {local} 中未找到权重文件，下载可能未完成。\n"
                    f"请重新下载：\n"
                    f"    $env:HF_ENDPOINT='https://hf-mirror.com'\n"
                    f"    python -c \"from huggingface_hub import snapshot_download;"
                    f" snapshot_download('{self.model_name}')\""
                )
            return str(local)

        if settings.embedding.offline:
            raise RuntimeError(
                f"离线模式下未在本地找到精排模型 {self.model_name}。\n"
                f"请先下载（国内建议走镜像）：\n"
                f"    $env:HF_ENDPOINT='https://hf-mirror.com'\n"
                f"    python -c \"from huggingface_hub import snapshot_download;"
                f" snapshot_download('{self.model_name}')\""
            )
        return self.model_name

    @property
    def size(self) -> int:
        return 1 if self._loaded else 0

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model_name,
            "backend": self.backend,
            "device": self._device,
            "loaded": self._loaded,
            "normalize_score": self.normalize_score,
            "load_seconds": round(self.load_seconds, 3),
            "last_seconds": round(self.last_seconds, 3),
        }
    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """
        对 (query, doc) 对打分，返回原始分数列表。

        CrossEncoder 输出 logits（可为负），FlagReranker 默认输出 float；
        两种后端返回格式不同（ndarray / list / 标量），这里统一成 float 列表。
        """
        if not pairs:
            return []
        self.load()
        cfg = settings.rerank

        if self.backend == "flag_embedding":
            raw = self._model.compute_score([list(p) for p in pairs], normalize=False)
        else:
            raw = self._model.predict(
                [list(p) for p in pairs],
                batch_size=cfg.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            )

        if isinstance(raw, (int, float)):
            return [float(raw)]
        # numpy 标量数组 / 嵌套数组统一展平为一维浮点列表
        out: list[float] = []
        for item in raw:
            if isinstance(item, (list, tuple)):
                out.append(float(item[0]))
            else:
                out.append(float(item))
        return out

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """
        精排：对候选重新打分并返回 Top-k。

        先对全部候选打分、再排序、最后截断到 k，排序与 k 无关，因此更小档位的结果是
        更大档位结果的**前缀**（Top-k 消融据此只跑一遍最大档位再截断）。

        :param query: 用户查询
        :param candidates: 召回阶段的候选（通常来自 HybridRetriever）
        :param top_k: 保留条数，默认 ``rerank.top_k``
        :returns: 新的 RetrievalResult 列表，``rerank_score`` 为精排分数，
                  ``rank`` 为精排后的名次，``fused_score`` 保留召回阶段分数以便对比
        """
        cfg = settings.rerank
        k = top_k or cfg.top_k
        if not candidates:
            return []

        t0 = time.perf_counter()
        pairs = [(query, c.text) for c in candidates]
        scores = self.score_pairs(pairs)

        if self.normalize_score:
            scores = [_sigmoid(s) for s in scores]

        scored: list[RetrievalResult] = []
        for item, score in zip(candidates, scores):
            if score < cfg.score_threshold:
                continue
            scored.append(
                RetrievalResult(
                    chunk_id=item.chunk_id,
                    text=item.text,
                    score=score,  # 精排后 score 即精排分数，保持语义直观
                    metadata=dict(item.metadata),
                    retriever=f"{item.retriever}+{self.name}" if item.retriever else self.name,
                    rank=0,
                    fused_score=item.fused_score if item.fused_score is not None else item.score,
                    rerank_score=score,
                    matched_by=list(item.matched_by),
                )
            )

        scored.sort(key=lambda r: -r.final_score)
        scored = scored[:k]
        for i, item in enumerate(scored, 1):
            item.rank = i

        self.last_seconds = time.perf_counter() - t0
        logger.debug(
            "精排: 候选=%d -> 输出=%d 耗时=%.3fs 分数区间=[%.4f, %.4f]",
            len(candidates), len(scored), self.last_seconds,
            min(scores) if scores else 0.0, max(scores) if scores else 0.0,
        )
        return scored

    def rerank_batch(
        self,
        queries: Sequence[str],
        candidates_list: Sequence[Sequence[RetrievalResult]],
        top_k: int | None = None,
        show_progress: bool = False,
    ) -> list[list[RetrievalResult]]:
        iterator: Sequence[Any] = list(zip(queries, candidates_list))
        if show_progress:
            from tqdm import tqdm

            iterator = tqdm(iterator, desc="精排", unit="问", ncols=90)
        return [self.rerank(q, cands, top_k) for q, cands in iterator]

    def score_of(self, query: str, texts: Sequence[str]) -> list[float]:
        """
        仅对文本打分（不涉及 RetrievalResult），便于快速验证模型是否可用：
        相关文档的分数应显著高于无关文档。
        """
        return self.score_pairs([(query, t) for t in texts])



_RERANKER: Reranker | None = None


def get_reranker() -> Reranker:
    """
    获取全局精排器单例。

    模型加载要 7~15 秒、占 2 GB 内存，不能在每次请求里重新构造：
    FastAPI 启动时调一次 ``warmup_reranker()`` 预热，后续请求直接复用。
    """
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = Reranker()
    return _RERANKER


def warmup_reranker() -> float:
    """预热全局精排器，返回加载耗时秒数。"""
    return get_reranker().load()
