"""交叉编码器精排。

对混合检索得到的候选重新打分并返回前若干条。双编码器分别编码查询与文档，只能判断大致相关；交叉编码器把查询与文档拼接后整体送入模型，准确度更高但计算量大，因此先召回再精排。分数为 logits，可为负，排序只看相对大小；需要展示百分比或按阈值过滤时再统一做 sigmoid 归一化。不同查询之间的分数不可直接比较。加载后端可选 cross_encoder 与 flag_embedding。
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from config import settings
from src.retrieval.base import RetrievalResult, get_logger

logger = get_logger(__name__)


def resolve_local_reranker(model_name: str) -> Path | None:
    """在本地模型缓存中查找精排模型，不发起网络请求。"""
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


class _TransformerCrossEncoder:
    """用 transformers 直接对查询与文档打分，替代句向量模型库的交叉编码器。

    这样可避免导入句向量模型库时连带加载训练与评估模块，显著缩短启动时间。该模型是单标签回归头，该实现保留原始 logits，使分数阈值能真正过滤无关候选；需要 0 到 1 分数时由归一化开关统一做一次 sigmoid。
    """

    def __init__(self, model_ref: str, device: str, max_length: int) -> None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(model_ref)
        self._model = (
            AutoModelForSequenceClassification.from_pretrained(model_ref).to(device).eval()
        )
        self._device = device
        self._max_length = max_length

    def predict(
        self,
        pairs: Sequence[Sequence[str]],
        *,
        batch_size: int = 32,
        show_progress_bar: bool = False,
        convert_to_numpy: bool = True,
    ):
        """打分，签名与交叉编码器的预测接口兼容，返回一维分数数组。"""
        import numpy as np
        import torch

        if not pairs:
            return np.zeros((0,), dtype="float32")

        bs = max(1, int(batch_size))
        batches = range(0, len(pairs), bs)
        if show_progress_bar:
            from tqdm import tqdm

            batches = tqdm(batches, desc="精排", unit="批", ncols=90)

        scores = []
        with torch.inference_mode():
            for start in batches:
                batch = [[str(q), str(d)] for q, d in pairs[start : start + bs]]
                features = self._tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=self._max_length,
                    return_tensors="pt",
                )
                features = {k: v.to(self._device) for k, v in features.items()}
                logits = self._model(**features).logits
                # 单标签回归头取唯一 logit，多标签时取最后一维
                scores.append(logits[:, -1].detach().cpu().numpy())

        values = np.concatenate(scores, axis=0)
        return values if convert_to_numpy else values.tolist()


class Reranker:
    """
    交叉编码器精排器，模型按需加载并可跨查询复用。
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
        self._load_lock = threading.RLock()
        self.load_seconds: float = 0.0
        self.last_seconds: float = 0.0
    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> float:
        """加载模型并返回耗时秒数，重复调用直接返回零。

        预热线程与首个查询可能同时触发加载，加锁保证只加载一次，避免并发读取同一份模型。
        """
        with self._load_lock:
            if self._loaded:
                return 0.0
            t0 = time.perf_counter()
            cfg = settings.rerank
            device = self._pick_device()

            if self.backend == "flag_embedding":
                from FlagEmbedding import FlagReranker

                self._model = FlagReranker(
                    self.model_name,
                    use_fp16=(device == "cuda" and cfg.use_fp16),
                )
            else:
                ref = self._resolve_ref()
                self._model = _TransformerCrossEncoder(ref, device=device, max_length=cfg.max_length)

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
        解析模型引用，优先使用本地快照，否则使用仓库名。

        模型文件不完整时给出明确提示，避免抛出难以理解的权重解析错误。
        """
        local = resolve_local_reranker(self.model_name)
        if local is not None:
            # 下载中断会留下不完整文件，需确认权重确实存在
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
        对查询与文档对打分，返回原始分数列表。

        两种后端返回格式不同，这里统一转换为浮点列表。
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
        # 标量数组与嵌套数组统一展平为一维浮点列表
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
        精排，对候选重新打分并返回前若干条。

        先对全部候选打分再排序，最后截断到指定条数，排序结果与条数无关，因此较小条数的结果总是较大条数结果的前缀。返回新的结果列表，其中精排分数单独保留，召回阶段的分数也一并保留以便对比。
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
                    score=score,  # 精排后分数即精排分数
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
        """批量精排，供评估使用。"""
        iterator: Sequence[Any] = list(zip(queries, candidates_list))
        if show_progress:
            from tqdm import tqdm

            iterator = tqdm(iterator, desc="精排", unit="问", ncols=90)
        return [self.rerank(q, cands, top_k) for q, cands in iterator]

    def score_of(self, query: str, texts: Sequence[str]) -> list[float]:
        """
        仅对文本打分，便于快速验证模型是否可用：相关文档的分数应显著高于无关文档。
        """
        return self.score_pairs([(query, t) for t in texts])



_RERANKER: Reranker | None = None


def get_reranker() -> Reranker:
    """
    获取全局精排器单例。

    模型加载耗时较长且占用较多内存，不能每次请求重新构造，服务启动时预热一次，之后的请求复用。
    """
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = Reranker()
    return _RERANKER


def warmup_reranker() -> float:
    """预热全局精排器，返回加载耗时秒数。"""
    return get_reranker().load()
