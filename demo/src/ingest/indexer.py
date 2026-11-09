"""索引构建，为混合检索准备稠密与稀疏两套索引。

稠密索引用 Chroma 加向量模型，集合名带分块策略后缀；稀疏索引用 BM25，按策略名单独落盘。BM25 只保存词频统计，不保存原文，因此 pickle 中同时保存原文块、分词结果与构建信息。原文块与分词结果必须严格一一对应，所有写入路径都通过同一份分块数据构造，避免检索结果错位。
"""

from __future__ import annotations

import json
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from config import configure_chroma_telemetry, settings
from src.ingest.splitter import Chunk, ChunkResult, load_chunks, chunks_file

# BM25 索引文件的格式版本
BM25_FORMAT_VERSION = 1

# 中文检索停用词表。
#
# 不能只靠文档频率过滤：BM25 的逆文档频率与语料相关，功能词若在本语料中不常见，权重反而偏高。例如查询词完全不存在时，不、存在这类词仍会被保留，临近最高分并返回无关结果。因此需要显式列出功能词，与文档频率过滤配合使用，两者条件取并集。
_STOPWORDS: frozenset[str] = frozenset(
    """
的 地 得 了 着 过 是 有 在 和 与 及 或 而 但 却 则 就 都 也 还 又 很 太 更 最 才 只
被 把 给 让 使 对 从 向 往 于 为 以 之 其 此 该 本 各 每 某 这 那 这些 那些 这个 那个
什么 怎么 怎样 如何 哪些 哪个 哪里 多少 多久 为什么 是否 能否 可以 应该 需要 应当
一个 一些 一种 一样 以及 并且 但是 因为 所以 如果 虽然 然后 而且 或者 不过 只是 还是
不 没 没有 无 非 未 别 请 我 你 他 她 它 我们 你们 他们 自己 大家
一 二 三 四 五 六 七 八 九 十 个 种 项 条 款 次 些 位 名 件 点 部分
要 会 能 可 好 多 少 大 小 上 下 中 内 外 前 后 时 用 做 说 看 想 知道 了解
""".split()
)



def collection_name_for_strategy(strategy: str, base: str | None = None) -> str:
    """每种分块策略使用独立的 Chroma 集合，避免互相覆盖。

    Chroma 集合名限制为 3 到 63 个字符，只允许字母、数字、下划线与短横线，且首尾须为字母或数字。
    """
    base_name = base or settings.retrieval.collection_name
    name = f"{base_name}_{strategy}".replace(".", "_").replace(" ", "_")
    return name[:63].strip("_-")


def bm25_path(strategy: str, directory: str | Path | None = None) -> Path:
    """BM25 索引文件路径，文件名按策略名区分。"""
    base = Path(directory) if directory else settings.paths.bm25_dir
    return base / f"bm25_{strategy}.pkl"



def tokenize(
    text: str,
    method: str | None = None,
) -> list[str]:
    """分词，供 BM25 使用。

    可选 jieba 中文分词、字符切分与空白切分三种方式。BM25 是词袋模型，中文不分词等于按字匹配，效果明显变差，因此默认使用 jieba。纯空白与纯标点的词会被过滤，避免噪声进入词频统计。
    """
    mode = (method or settings.retrieval.bm25_tokenizer).strip().lower()
    if mode == "jieba":
        import jieba

        tokens = [t.strip() for t in jieba.lcut(text)]
    elif mode == "char":
        tokens = list(text)
    else:
        tokens = text.split()

    return [t for t in tokens if t and not t.isspace() and any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in t)]


def tokenize_corpus(texts: Sequence[str], method: str | None = None, show_progress: bool = False) -> list[list[str]]:
    # jieba 首次调用需要构建词典，先预热以免进度条长时间停在起点
    if (method or settings.retrieval.bm25_tokenizer) == "jieba":
        import jieba

        jieba.initialize()

    iterator: Iterable[str] = texts
    if show_progress:
        from tqdm import tqdm

        iterator = tqdm(texts, desc="BM25 分词", unit="块", ncols=90)
    return [tokenize(t, method) for t in iterator]



def resolve_local_snapshot(model_name: str) -> Path | None:
    """查询模型是否已在本地缓存中，返回快照目录。

    只在本地查找，不发起网络请求；否则无外网环境下会长时间挂起。
    """
    path = Path(model_name)
    if path.exists():
        return path
    try:
        from huggingface_hub import snapshot_download

        return Path(snapshot_download(model_name, local_files_only=True))
    except Exception:  # noqa: BLE001 - 未缓存时返回 None，由调用方给出提示
        return None


class _TransformerEmbedder:
    """直接用 transformers 前向编码，替代句向量模型库的模型包装。

    这样可避免导入句向量模型库时连带加载训练与评估模块，缩短启动时间。语义与其结果一致：取最后一层隐藏状态的第一个位置即 CLS 池化，再按需做 L2 归一化。
    """

    def __init__(self, model_ref: str, device: str, max_seq_length: int) -> None:
        from transformers import AutoModel, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(model_ref)
        self._model = AutoModel.from_pretrained(model_ref).to(device).eval()
        self._device = device
        self.max_seq_length = max_seq_length

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int = 32,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
    ):
        """批量编码为向量，签名与 sentence-transformers 的编码接口兼容。"""
        import numpy as np
        import torch

        texts = [str(t) for t in texts]
        dim = int(self._model.config.hidden_size)
        if not texts:
            return np.zeros((0, dim), dtype="float32")

        bs = max(1, int(batch_size))
        batches = range(0, len(texts), bs)
        if show_progress_bar:
            from tqdm import tqdm

            batches = tqdm(batches, desc="向量化", unit="批", ncols=90)

        chunks = []
        with torch.inference_mode():
            for start in batches:
                batch = texts[start : start + bs]
                features = self._tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=self.max_seq_length,
                    return_tensors="pt",
                )
                features = {k: v.to(self._device) for k, v in features.items()}
                hidden = self._model(**features).last_hidden_state[:, 0, :]
                if normalize_embeddings:
                    hidden = torch.nn.functional.normalize(hidden, p=2, dim=1)
                chunks.append(hidden.detach().cpu().numpy())

        vectors = np.concatenate(chunks, axis=0)
        return vectors if convert_to_numpy else vectors.tolist()


def load_embedder(device: str | None = None, *, offline: bool | None = None):
    """加载向量模型，走 transformers 前向，不导入句向量模型库。

    优先离线加载：加载时会联网校验缓存，国内网络下会连接超时并反复重试，表现为长时间卡住，因此默认开启离线模式，本地有快照即完全不联网。加载前先预检，本地没有模型时立即抛出说明清楚的错误，不进入重试流程。文档侧不加检索指令前缀，只有查询侧添加。
    """
    import os

    import torch

    cfg = settings.embedding
    use_offline = cfg.offline if offline is None else offline

    # CPU 推理时显式用满物理核心，torch 默认只启用部分线程
    if not torch.cuda.is_available():
        torch.set_num_threads(os.cpu_count() or torch.get_num_threads())

    local = resolve_local_snapshot(cfg.resolved_model)
    if local is not None and use_offline:
        # 直接指向本地快照，彻底避免网络校验
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        model_ref: str = str(local)
    elif local is not None:
        model_ref = str(local)
    else:
        if use_offline:
            raise RuntimeError(
                f"离线模式下未在本地找到模型 {cfg.resolved_model}。\n"
                f"请先下载（国内建议走镜像）：\n"
                f"    $env:HF_ENDPOINT='https://hf-mirror.com'\n"
                f"    uv pip install huggingface_hub\n"
                f"    python -c \"from huggingface_hub import snapshot_download;"
                f" snapshot_download('{cfg.resolved_model}')\"\n"
                f"或关闭离线模式：RAG_EMBEDDING__OFFLINE=false"
            )
        # 未缓存且允许联网，交给模型库自行下载
        model_ref = cfg.resolved_model

    if device and device != "auto":
        target = device
    elif torch.cuda.is_available():
        target = "cuda"
    else:
        target = "cpu"

    model = _TransformerEmbedder(model_ref, target, cfg.max_length)
    return model, target


def embed_texts(
    embedder: Any,
    texts: Sequence[str],
    *,
    batch_size: int | None = None,
    show_progress: bool = True,
    desc: str = "向量化",
):
    """生成归一化向量。

    归一化是必须的：Chroma 使用余弦距离，只有向量已归一化，余弦相似度才等价于点积，检索结果才正确。
    """
    bs = batch_size or settings.embedding.batch_size
    return embedder.encode(
        list(texts),
        batch_size=bs,
        normalize_embeddings=settings.embedding.normalize_embeddings,
        convert_to_numpy=True,
        show_progress_bar=show_progress,
    )



def flatten_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """把元数据规整为向量库可接受的标量类型。

    向量库的元数据只允许字符串、整数、浮点数与布尔值，不允许空值、列表与嵌套字典，因此写入前显式转换，而不是等写入时报错。
    """
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, (list, tuple, set)):
            out[key] = json.dumps(list(value), ensure_ascii=False)
        elif isinstance(value, dict):
            out[key] = json.dumps(value, ensure_ascii=False)
        else:
            out[key] = str(value)
    return out



def get_chroma_client():
    """创建或复用本地持久化的向量库客户端。"""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    configure_chroma_telemetry()
    return chromadb.PersistentClient(
        path=str(settings.paths.chroma_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def check_embedding_health(vectors: Any, *, sample: int = 50) -> dict[str, Any]:
    """写入前校验向量质量，任一指标不达标即抛出异常。

    检查三项：非零维度比例，真实模型应接近全部维度；平均模长，应接近 1，说明已归一化；两两余弦最大值，用于发现退化为几乎相同的向量。假向量在库内自洽，但与真实查询向量近乎正交，只会表现为向量检索时好时坏，表面看不出错误，因此必须拦截。为控制耗时仅抽样检查。返回检查指标。
    """
    import numpy as np

    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] == 0:
        raise ValueError(f"向量矩阵形状异常: {arr.shape}")

    take = min(sample, arr.shape[0])
    sub = arr[:take]
    dim = arr.shape[1]

    nonzero_ratio = float((np.abs(sub) > 1e-9).sum(axis=1).mean() / dim)
    norms = np.linalg.norm(sub, axis=1)
    norm_mean = float(norms.mean())

    # 两两余弦的最大值，排除对角线，用于发现所有向量几乎相同的情况
    if take >= 2:
        unit = sub / np.maximum(norms[:, None], 1e-12)
        sim = unit @ unit.T
        np.fill_diagonal(sim, -1.0)
        max_pairwise = float(sim.max())
    else:
        max_pairwise = 0.0

    metrics = {
        "dim": dim,
        "sampled": take,
        "nonzero_ratio": round(nonzero_ratio, 4),
        "norm_mean": round(norm_mean, 4),
        "max_pairwise_cosine": round(max_pairwise, 4),
    }

    problems: list[str] = []
    if nonzero_ratio < 0.5:
        problems.append(
            f"非零维度比例仅 {nonzero_ratio:.1%}（真实嵌入模型应接近 100%），"
            f"疑似使用了错误的 embedder（例如测试用的假向量）"
        )
    if abs(norm_mean - 1.0) > 0.05:
        problems.append(
            f"向量未归一化（平均模长 {norm_mean:.4f}），"
            f"请确认 embedding.normalize_embeddings=true"
        )
    if max_pairwise > 0.999:
        problems.append(
            f"存在几乎相同的向量（最大两两余弦 {max_pairwise:.4f}），嵌入模型输出异常"
        )

    if problems:
        raise ValueError(
            "嵌入质量校验未通过，已阻止写入索引（避免污染向量库）：\n"
            + "\n".join(f"  - {p}" for p in problems)
            + f"\n体检指标: {metrics}"
        )
    return metrics


def build_vector_index(
    chunks: Sequence[Chunk],
    strategy: str,
    *,
    embedder: Any = None,
    rebuild: bool = True,
    batch_size: int | None = None,
    show_progress: bool = True,
    collection_name: str | None = None,
    skip_health_check: bool = False,
) -> dict[str, Any]:
    """构建向量索引。

    策略名决定集合名；重建开关为真时先删除同名集合再写入，保证结果幂等；嵌入模型已加载时可直接复用。跳过质量校验仅供测试，正常构建必须校验，否则退化向量会静默污染索引。返回构建统计信息。
    """
    client = get_chroma_client()
    name = collection_name or collection_name_for_strategy(strategy)

    if rebuild:
        try:
            client.delete_collection(name)
        except Exception:  # noqa: BLE001 - 集合不存在时忽略
            pass

    collection = client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine", "strategy": strategy},
    )

    if not chunks:
        return {"collection": name, "count": 0, "dimension": 0, "seconds": 0.0}

    texts = [c.text for c in chunks]
    ids = [c.chunk_id for c in chunks]
    metas = [flatten_metadata(c.metadata) for c in chunks]

    if embedder is None:
        embedder, _ = load_embedder()

    t0 = time.time()
    vectors = embed_texts(
        embedder,
        texts,
        batch_size=batch_size,
        show_progress=show_progress,
        desc=f"向量化[{strategy}]",
    )

    # 写入前检查，拦截退化向量，避免污染真实索引
    health = None if skip_health_check else check_embedding_health(vectors)

    # 分批写入，避免一次性提交过多数据占用内存
    bs = batch_size or settings.embedding.batch_size
    if show_progress:
        from tqdm import tqdm

        iterator = tqdm(range(0, len(ids), bs), desc=f"写入 Chroma[{strategy}]", unit="批", ncols=90)
    else:
        iterator = range(0, len(ids), bs)

    for start in iterator:
        end = start + bs
        collection.upsert(
            ids=ids[start:end],
            documents=texts[start:end],
            embeddings=[v.tolist() for v in vectors[start:end]],
            metadatas=metas[start:end],
        )

    return {
        "collection": name,
        "count": collection.count(),
        "dimension": int(len(vectors[0])) if len(vectors) else 0,
        "seconds": round(time.time() - t0, 2),
        "embedding_health": health,
    }



@dataclass
class BM25Index:
    """BM25 索引的内存表示，同时是 pickle 的落盘结构。

    原文块与分词结果顺序一一对应是硬性约束，不能单独修改其中一个。
    """

    documents: list[dict[str, Any]] = field(default_factory=list)
    tokenized: list[list[str]] = field(default_factory=list)
    strategy: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    _bm25: Any = None
    _df: dict[str, int] | None = None

    def __len__(self) -> int:
        return len(self.documents)

    @property
    def bm25(self):
        """按需构建 BM25 模型，pickle 中不保存模型对象，避免与库版本耦合。"""
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(
                self.tokenized,
                k1=settings.retrieval.bm25_k1,
                b=settings.retrieval.bm25_b,
            )
        return self._bm25

    def _df_map(self) -> dict[str, int]:
        """文档频率表，记录每个词出现在多少篇文档中，首次使用时构建并缓存。"""
        if self._df is None:
            df: dict[str, int] = {}
            for tokens in self.tokenized:
                for term in set(tokens):
                    df[term] = df.get(term, 0) + 1
            self._df = df
        return self._df

    def informative_terms(self, query: str) -> list[str]:
        """从查询中筛出有区分度的词，剔除停用词与语料中不存在的词。

        BM25 会把负的逆文档频率截断为很小的正数，于是在多数文档中普遍出现的高频词反而给每篇文档加分，用完全不含语料词的查询也会得到正分，直接污染融合结果。

        过滤条件取并集，缺一不可：显式停用词表，即使文档频率低也不参与匹配；文档频率上界，超过比例上限的词视为停用词；文档频率下界，语料中不存在的词对检索无贡献。所有词都被剔除时返回空列表，下游可直接返回空结果，而不是把噪声文档当作命中。
        """
        cfg = settings.retrieval
        tokens = tokenize(query)
        if not tokens:
            return []
        df = self._df_map()
        n = max(len(self.documents), 1)
        limit = cfg.bm25_max_df_ratio * n

        kept: list[str] = []
        for term in tokens:
            if term in _STOPWORDS:
                continue
            freq = df.get(term, 0)
            if freq < cfg.bm25_min_term_df:
                continue
            if freq > limit:
                continue
            kept.append(term)
        return kept

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """返回语料下标与分数的二元组列表，按分数降序，只保留正分。

        先用有效查询词过滤，若全部被剔除，说明该查询在本语料中没有匹配依据，直接返回空列表。
        """
        import numpy as np

        terms = self.informative_terms(query)
        if not terms:
            return []

        scores = self.bm25.get_scores(terms)
        arr = np.asarray(scores, dtype=float)
        if arr.size == 0 or not arr.any():
            return []

        # 过滤仅被截断后的逆文档频率抬高到正数的噪声文档，分数须明显高于停用词带来的水位线
        floor = settings.retrieval.bm25_score_floor
        k = min(top_k, arr.size)
        idx = np.argpartition(-arr, k - 1)[:k]
        idx = idx[np.argsort(-arr[idx])]
        return [(int(i), float(arr[i])) for i in idx if arr[i] > floor]


def build_bm25_index(
    chunks: Sequence[Chunk],
    strategy: str,
    *,
    show_progress: bool = True,
    path: str | Path | None = None,
) -> tuple[BM25Index, Path]:
    """构建 BM25 索引并写入 pickle 文件。"""
    texts = [c.text for c in chunks]
    tokenized = tokenize_corpus(texts, show_progress=show_progress)

    index = BM25Index(
        documents=[{"text": c.text, "metadata": dict(c.metadata)} for c in chunks],
        tokenized=tokenized,
        strategy=strategy,
        meta={
            "format_version": BM25_FORMAT_VERSION,
            "strategy": strategy,
            "tokenizer": settings.retrieval.bm25_tokenizer,
            "k1": settings.retrieval.bm25_k1,
            "b": settings.retrieval.bm25_b,
            "count": len(chunks),
            "built_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    target = Path(path) if path else bm25_path(strategy)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".pkl.tmp")
    with tmp.open("wb") as fh:
        pickle.dump(index, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(target)
    return index, target


def load_bm25_index(strategy: str, path: str | Path | None = None) -> BM25Index:
    target = Path(path) if path else bm25_path(strategy)
    if not target.exists():
        raise FileNotFoundError(
            f"未找到 BM25 索引: {target}\n"
            f"请先执行：python -m src.ingest.cli index --strategy {strategy}"
        )
    with target.open("rb") as fh:
        index: BM25Index = pickle.load(fh)
    if len(index.documents) != len(index.tokenized):
        raise ValueError(
            f"BM25 索引结构损坏：documents({len(index.documents)}) 与 "
            f"tokenized({len(index.tokenized)}) 长度不一致，请重建索引"
        )
    return index



@dataclass
class IndexReport:
    strategy: str
    chunks: int = 0
    vector: dict[str, Any] = field(default_factory=dict)
    bm25: dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0

    def summary(self) -> str:
        return (
            f"[{self.strategy}] {self.chunks} 块 | "
            f"Chroma 集合 {self.vector.get('collection')} {self.vector.get('count')} 条 "
            f"({self.vector.get('seconds')}s) | "
            f"BM25 {self.bm25.get('count')} 条 -> {self.bm25.get('path')} | "
            f"总耗时 {self.elapsed}s"
        )


def build_index(
    strategy: str | None = None,
    *,
    rebuild: bool = True,
    embedder: Any = None,
    batch_size: int | None = None,
    show_progress: bool = True,
    source: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
    skip_health_check: bool = False,
) -> IndexReport:
    """构建指定分块策略下的双索引，返回构建报告。

    策略名可取固定长度或递归语义，默认取配置值；重建开关为真时先清空再写入；已加载的模型可复用，批量重建两种策略时尤其有用；分块文件路径可显式指定，默认按策略名定位。跳过质量校验仅供测试。
    """
    name = (strategy or settings.chunk.default_strategy).strip().lower()
    if name not in ("fixed", "recursive"):
        raise ValueError(f"未知分块策略 '{strategy}'，可选：fixed | recursive")

    t0 = time.time()
    if source:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        chunks = [
            Chunk(text=item["text"], metadata=dict(item.get("metadata", {})))
            for item in payload.get("chunks", [])
        ]
        result = ChunkResult(chunks=chunks, strategy=payload.get("strategy", name), params={})
        origin = str(source)
    else:
        result = load_chunks(name)
        origin = str(chunks_file(name))

    if progress:
        progress(f"读取分块文件: {origin}（{result.total} 块）")

    if result.total == 0:
        raise ValueError(
            f"分块文件 {origin} 中没有数据。请先确认 data/raw/ 下有 PDF 并执行入库。"
        )

    vector_stats = build_vector_index(
        result.chunks,
        name,
        embedder=embedder,
        rebuild=rebuild,
        batch_size=batch_size,
        show_progress=show_progress,
        skip_health_check=skip_health_check,
    )

    _, path = build_bm25_index(result.chunks, name, show_progress=show_progress)
    bm25_stats = {"count": result.total, "path": path.name, "size_kb": round(path.stat().st_size / 1024, 1)}

    report = IndexReport(
        strategy=name,
        chunks=result.total,
        vector=vector_stats,
        bm25=bm25_stats,
        elapsed=round(time.time() - t0, 2),
    )
    if progress:
        progress(report.summary())
    return report


def index_stats(strategy: str | None = None) -> dict[str, Any]:
    """查看指定策略的索引现状，不触发重建，供命令行与前端展示。"""
    name = (strategy or settings.chunk.default_strategy).strip().lower()
    info: dict[str, Any] = {"strategy": name, "chunks_file": None, "chunks": 0, "collection": None,
                            "vector_count": 0, "bm25_file": None, "bm25_count": 0}

    cf = chunks_file(name)
    if cf.exists():
        info["chunks_file"] = cf.name
        try:
            info["chunks"] = len(json.loads(cf.read_text(encoding="utf-8")).get("chunks", []))
        except Exception:  # noqa: BLE001
            pass

    coll = collection_name_for_strategy(name)
    try:
        client = get_chroma_client()
        info["collection"] = coll
        info["vector_count"] = client.get_collection(coll).count()
    except Exception:  # noqa: BLE001 - 集合不存在
        info["collection"] = coll

    bp = bm25_path(name)
    if bp.exists():
        info["bm25_file"] = bp.name
        try:
            info["bm25_count"] = len(load_bm25_index(name))
        except Exception:  # noqa: BLE001
            pass
    return info


def available_strategies() -> list[str]:
    return list(settings.chunk.available_strategies)
