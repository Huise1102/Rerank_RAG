"""阶段 1 —— 索引构建（indexer.py）：为阶段 2 的混合检索构建两套索引（按知识库隔离）。

    稠密  Chroma + bge-large-zh-v1.5（normalize_embeddings=True）-> indexes/chroma/
          集合名 ``kb_<kb_id>_<strategy>``
    稀疏  BM25Okapi（jieba 分词）-> indexes/bm25/kb_<kb_id>.pkl

``BM25Okapi`` 本身只存词频统计、不存原文，所以 pickle 里必须同时存 documents（原文块，
顺序与语料**严格一致**）、tokenized（打分依赖）与 meta（策略/模型/构建时间，便于校验
索引与配置是否匹配）。``documents[i]`` 必须对应 ``corpus[i]``，否则检索结果张冠李戴 ——
所有写入路径都通过同一个 ``corpus`` 构造来保证。

多知识库隔离：集合名带 ``kb_<kb_id>_`` 前缀，BM25 文件落 ``kb_<kb_id>.pkl``，
分块文件落 ``data/processed/kb_<kb_id>/``，与 demo/ 的索引、数据零耦合。

主流程::

    build_kb_index(kb_id=1, strategy="recursive")     # 一步构建双索引
"""

from __future__ import annotations

import json
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from src.core.config import configure_chroma_telemetry, settings
from src.ingest.splitter import Chunk, ChunkResult, chunks_file, load_chunks

#: BM25 pickle 的格式版本
BM25_FORMAT_VERSION = 1

#: 中文检索停用词表（虚词、代词、疑问词、通用动词）。
#:
#: 不能只靠文档频率（df）过滤：BM25 的 IDF 是**语料相关**的，功能词在本语料里若不常见，
#: IDF 反而很高。实测查询「zzzzz不存在的词zzzzz」经 df 过滤后仍保留「不」「存在」
#: （分别出现在 103/15 篇文档中），Top-1 拿到 4.66 分并返回毫不相关的结果。
#: 因此必须**显式列出功能词**，与 df 过滤配合使用（两者是并集关系）。
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



def collection_name_for_kb(kb_id: int, strategy: str) -> str:
    """每个知识库、每种分块策略使用独立的 Chroma 集合，避免互相覆盖。

    命名规则：``kb_<kb_id>_<strategy>``。Chroma 集合名限制：3-63 字符，只允许
    字母数字与 ``_``、``-``，且首尾为字母数字，故做一次规整。
    """
    name = f"kb_{int(kb_id)}_{strategy}".replace(".", "_").replace(" ", "_")
    return name[:63].strip("_-")


def bm25_path_for_kb(kb_id: int, strategy: str | None = None) -> Path:
    """BM25 索引文件路径：indexes/bm25/kb_<kb_id>.pkl

    每个知识库固定使用一种分块策略，故文件名只带 kb_id（``strategy`` 仅为接口兼容保留）。
    """
    return settings.paths.bm25_dir / f"kb_{int(kb_id)}.pkl"



def tokenize(
    text: str,
    method: str | None = None,
) -> list[str]:
    """分词，供 BM25 使用。

    - ``jieba`` 中文分词（默认）；BM25 是词袋模型，中文不分词等于按字匹配，效果显著变差
    - ``char`` 按字符切分，无需词典，作为对照基线；``whitespace`` 按空白切分，适合英文
    - 过滤纯空白与纯标点 token，避免噪声进入词频统计
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
    # jieba 首次调用需要建词典，先预热以免进度条卡在 0%
    if (method or settings.retrieval.bm25_tokenizer) == "jieba":
        import jieba

        jieba.initialize()

    iterator: Iterable[str] = texts
    if show_progress:
        from tqdm import tqdm

        iterator = tqdm(texts, desc="BM25 分词", unit="块", ncols=90)
    return [tokenize(t, method) for t in iterator]



def resolve_local_snapshot(model_name: str) -> Path | None:
    """查询模型是否已在本地 HuggingFace 缓存中，返回快照目录。

    用 ``local_files_only=True``：**绝不触发网络请求**；若改成联网查询，无外网环境下会长时间挂起。
    """
    path = Path(model_name)
    if path.exists():
        return path
    try:
        from huggingface_hub import snapshot_download

        return Path(snapshot_download(model_name, local_files_only=True))
    except Exception:  # noqa: BLE001 - 未缓存时返回 None，由调用方给出提示
        return None


def load_embedder(device: str | None = None, *, offline: bool | None = None):
    """加载 bge-large-zh-v1.5（sentence-transformers）。

    1. **离线优先**：sentence-transformers 加载时会访问 HuggingFace 校验缓存（即使文件已下全），
       国内网络下会 connect timeout 并触发 5 轮指数退避，表现为程序卡住几十分钟；
       因此默认走 ``HF_HUB_OFFLINE``，只要本地有快照就完全不联网。
    2. **加载前预检**：先用 ``local_files_only=True`` 确认本地有模型，没有则立刻抛出可操作的
       错误提示，而不是让程序卡在重试里。

    文档侧**不加**检索指令前缀，只有查询侧加（阶段 2 处理），这是 bge 官方推荐用法。
    """
    import os

    import torch
    from sentence_transformers import SentenceTransformer

    cfg = settings.embedding
    use_offline = cfg.offline if offline is None else offline

    # CPU 推理时显式用满物理核心：torch 默认只取一部分（实测 24 核机器上仅用 16 线程）
    if not torch.cuda.is_available():
        torch.set_num_threads(os.cpu_count() or torch.get_num_threads())

    local = resolve_local_snapshot(cfg.resolved_model)
    if local is not None and use_offline:
        # 直接指向本地快照，彻底避免任何网络校验
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
        # 未缓存且允许联网：交给 sentence-transformers 自行下载
        model_ref = cfg.resolved_model

    if device and device != "auto":
        target = device
    elif torch.cuda.is_available():
        target = "cuda"
    else:
        target = "cpu"

    model = SentenceTransformer(model_ref, device=target)
    model.max_seq_length = cfg.max_length
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

    ``normalize_embeddings=True`` 是必须的：Chroma 用余弦距离，只有向量已归一化时
    余弦相似度才等价于点积，检索结果才正确。
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
    """把元数据规整为 Chroma 可接受的类型。

    Chroma 的 metadata 只允许 str / int / float / bool（不允许 None、列表、嵌套字典），
    因此在写入前显式转换，而不是等写入时报错。
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
    """创建/复用本地持久化 Chroma 客户端（indexes/chroma/）。"""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    configure_chroma_telemetry()
    return chromadb.PersistentClient(
        path=str(settings.paths.chroma_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def check_embedding_health(vectors: Any, *, sample: int = 50) -> dict[str, Any]:
    """写入前校验向量质量，拦截"退化向量"：任一指标不达标即抛 ValueError。

    三项体检：① 非零维度比例（真实 BGE 接近 100%，稀疏向量说明用错了 embedder）；
    ② 平均模长应接近 1（配置要求 normalize_embeddings=True）；③ 两两余弦最大值。

    假 embedder（如测试用的字符哈希）产生的向量库内自洽（自查相似度 1.0），但与真实模型
    编码的查询完全正交（余弦≈0），只表现为"向量检索时好时坏"、表面看不出错误，故必须拦截。

    :param vectors: 嵌入矩阵（numpy 数组或列表）
    :param sample: 抽样条数（全量检查太慢，抽样已足够发现问题）
    :returns: 体检指标
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

    # 两两余弦的最大值（排除对角线）：用于发现"所有向量几乎相同"
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


def build_vector_index_for_kb(
    kb_id: int,
    chunks: Sequence[Chunk],
    strategy: str,
    *,
    embedder: Any = None,
    rebuild: bool = True,
    batch_size: int | None = None,
    show_progress: bool = True,
    skip_health_check: bool = False,
) -> dict[str, Any]:
    """构建知识库的 Chroma 向量索引（集合名 ``kb_<kb_id>_<strategy>``）。

    :param rebuild: True 表示先删除同名集合再重建（保证幂等、无残留）
    :param skip_health_check: **仅供测试使用**。跳过嵌入质量校验；生产路径必须校验，
        否则假 embedder 的退化向量会静默污染向量库（症状是"向量检索时好时坏"）
    :returns: 构建统计信息
    """
    client = get_chroma_client()
    name = collection_name_for_kb(kb_id, strategy)

    if rebuild:
        try:
            client.delete_collection(name)
        except Exception:  # noqa: BLE001 - 集合不存在时忽略
            pass

    collection = client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine", "strategy": strategy, "kb_id": int(kb_id)},
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
        desc=f"向量化[kb{kb_id}/{strategy}]",
    )

    # 写入前体检：拦住退化向量，避免污染真实索引
    health = None if skip_health_check else check_embedding_health(vectors)

    # 分批写入，避免一次性提交过多数据占用内存
    bs = batch_size or settings.embedding.batch_size
    if show_progress:
        from tqdm import tqdm

        iterator = tqdm(range(0, len(ids), bs), desc=f"写入 Chroma[kb{kb_id}/{strategy}]", unit="批", ncols=90)
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

    documents 与 tokenized 顺序一一对应是硬约束，切勿单独修改其中一个。
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
        """惰性构建 BM25Okapi（pickle 里不存模型对象，避免版本耦合）。"""
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(
                self.tokenized,
                k1=settings.retrieval.bm25_k1,
                b=settings.retrieval.bm25_b,
            )
        return self._bm25

    def _df_map(self) -> dict[str, int]:
        """文档频率表（每个词出现在多少篇文档中），首次使用时构建并缓存。"""
        if self._df is None:
            df: dict[str, int] = {}
            for tokens in self.tokenized:
                for term in set(tokens):
                    df[term] = df.get(term, 0) + 1
            self._df = df
        return self._df

    def informative_terms(self, query: str) -> list[str]:
        """从查询中筛出**有区分度**的词，剔除停用词与未登录词。

        ``rank_bm25.BM25Okapi`` 会把负 IDF 截断为一个很小的正数（epsilon，默认约 0.25），
        于是「的」「不」这类在 90% 文档中都出现的词**反而给每篇文档加分**；实测在 290 篇语料上
        用完全不含语料词的查询（``zzzzz不存在的词zzzzz``）检索，275 篇拿到正分且 Top-1 毫不相关，
        会直接污染混合检索的融合结果。

        过滤规则（两道并集，缺一不可）：显式停用词表（详见 ``_STOPWORDS``，即使 df 低、
        IDF 高也不参与匹配）；文档频率上界 ``df / N > bm25_max_df_ratio``；另外
        ``df < bm25_min_term_df`` 的查询词不在语料中，对检索无贡献，一并剔除。

        附带好处：查询词全被剔除时可直接返回空结果，而不是把噪声文档当"命中"喂给下游。
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
        """返回 [(语料下标, 分数)]，按分数降序，只保留正分。

        先用 ``informative_terms`` 剔除停用词/未登录词；若查询词全被剔除，说明该查询
        在本语料中没有有效匹配依据，直接返回空列表。
        """
        import numpy as np

        terms = self.informative_terms(query)
        if not terms:
            return []

        scores = self.bm25.get_scores(terms)
        arr = np.asarray(scores, dtype=float)
        if arr.size == 0 or not arr.any():
            return []

        # 过滤被"epsilon IDF"抬高到正数的噪声文档：分数须明显高于仅由停用词贡献的水位线
        floor = settings.retrieval.bm25_score_floor
        k = min(top_k, arr.size)
        idx = np.argpartition(-arr, k - 1)[:k]
        idx = idx[np.argsort(-arr[idx])]
        return [(int(i), float(arr[i])) for i in idx if arr[i] > floor]


def _make_bm25_index(chunks: Sequence[Chunk], strategy: str, *, show_progress: bool = True) -> BM25Index:
    """把分块列表构造成 ``BM25Index``（不落盘）。"""
    texts = [c.text for c in chunks]
    tokenized = tokenize_corpus(texts, show_progress=show_progress)
    return BM25Index(
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


def _save_bm25_index(index: BM25Index, path: Path) -> Path:
    """pickle 落盘（先写临时文件再替换）；写前清掉惰性缓存对象，避免版本耦合。"""
    index._bm25 = None
    index._df = None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pkl.tmp")
    with tmp.open("wb") as fh:
        pickle.dump(index, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    return path


def build_bm25_index_for_kb(
    kb_id: int,
    chunks: Sequence[Chunk],
    strategy: str,
    *,
    show_progress: bool = True,
) -> tuple[BM25Index, Path]:
    """构建知识库的 BM25 索引并 pickle 落盘到 ``indexes/bm25/kb_<kb_id>.pkl``。"""
    index = _make_bm25_index(chunks, strategy, show_progress=show_progress)
    target = _save_bm25_index(index, bm25_path_for_kb(kb_id, strategy))
    return index, target


def load_bm25_index_kb(kb_id: int, strategy: str | None = None) -> BM25Index:
    """加载知识库的 BM25 索引（自带 documents/tokenized 长度一致性校验）。"""
    target = bm25_path_for_kb(kb_id, strategy)
    if not target.exists():
        raise FileNotFoundError(
            f"未找到知识库 {kb_id} 的 BM25 索引: {target}\n"
            f"请先完成该知识库的文档入库。"
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
    kb_id: int
    strategy: str
    chunks: int = 0
    vector: dict[str, Any] = field(default_factory=dict)
    bm25: dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0

    def summary(self) -> str:
        return (
            f"[kb={self.kb_id}/{self.strategy}] {self.chunks} 块 | "
            f"Chroma 集合 {self.vector.get('collection')} {self.vector.get('count')} 条 "
            f"({self.vector.get('seconds')}s) | "
            f"BM25 {self.bm25.get('count')} 条 -> {self.bm25.get('path')} | "
            f"总耗时 {self.elapsed}s"
        )


def build_kb_index(
    kb_id: int,
    strategy: str | None = None,
    *,
    chunks: Sequence[Chunk] | None = None,
    rebuild: bool = True,
    embedder: Any = None,
    batch_size: int | None = None,
    show_progress: bool = True,
    source: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
    skip_health_check: bool = False,
) -> IndexReport:
    """构建指定知识库、指定分块策略下的双索引（向量 + BM25），检索阶段的入口。

    :param kb_id: 知识库 id；:param strategy: "fixed" | "recursive"，默认取 config.chunk.default_strategy
    :param chunks: 直接传入分块列表（入库链路用）；不传则从 ``chunks_<strategy>.json`` 读取
    :param rebuild: 是否先清空再重建（True 保证幂等）
    :param embedder: 复用已加载模型，避免重复加载；:returns: IndexReport
    """
    name = (strategy or settings.chunk.default_strategy).strip().lower()
    if name not in ("fixed", "recursive"):
        raise ValueError(f"未知分块策略 '{strategy}'，可选：fixed | recursive")

    t0 = time.time()
    if chunks is not None:
        result = ChunkResult(chunks=list(chunks), strategy=name, params={})
        origin = f"内存传入（{len(chunks)} 块）"
    elif source:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        result = ChunkResult(
            chunks=[Chunk(text=item["text"], metadata=dict(item.get("metadata", {})))
                    for item in payload.get("chunks", [])],
            strategy=payload.get("strategy", name),
            params={},
        )
        origin = str(source)
    else:
        result = load_chunks(kb_id, name)
        origin = str(chunks_file(kb_id, name))

    if progress:
        progress(f"读取分块文件: {origin}（{result.total} 块）")

    if result.total == 0:
        raise ValueError(f"知识库 {kb_id} 的分块数据为空（来源 {origin}），无法构建索引。")

    vector_stats = build_vector_index_for_kb(
        kb_id,
        result.chunks,
        name,
        embedder=embedder,
        rebuild=rebuild,
        batch_size=batch_size,
        show_progress=show_progress,
        skip_health_check=skip_health_check,
    )

    _, path = build_bm25_index_for_kb(kb_id, result.chunks, name, show_progress=show_progress)
    bm25_stats = {"count": result.total, "path": path.name, "size_kb": round(path.stat().st_size / 1024, 1)}

    report = IndexReport(
        kb_id=int(kb_id),
        strategy=name,
        chunks=result.total,
        vector=vector_stats,
        bm25=bm25_stats,
        elapsed=round(time.time() - t0, 2),
    )
    if progress:
        progress(report.summary())
    return report


def append_chunks_to_index(
    kb_id: int,
    strategy: str | None = None,
    chunks: Sequence[Chunk] | None = None,
    *,
    embedder: Any = None,
    batch_size: int | None = None,
    show_progress: bool = True,
    skip_health_check: bool = False,
) -> dict[str, Any]:
    """增量入库：把新增文档块追加到已有索引，不重建。

    向量侧用 ``upsert``（同 id 幂等覆盖）；BM25 侧读回已有 pickle 后追加
    documents/tokenized 再落盘。新增知识与旧知识在检索时同等参与。
    """
    name = (strategy or settings.chunk.default_strategy).strip().lower()
    if name not in ("fixed", "recursive"):
        raise ValueError(f"未知分块策略 '{strategy}'，可选：fixed | recursive")
    new_chunks = list(chunks or [])
    if not new_chunks:
        return {"kb_id": int(kb_id), "strategy": name, "added": 0}

    client = get_chroma_client()
    coll_name = collection_name_for_kb(kb_id, name)
    collection = client.get_or_create_collection(
        name=coll_name,
        metadata={"hnsw:space": "cosine", "strategy": name, "kb_id": int(kb_id)},
    )

    if embedder is None:
        embedder, _ = load_embedder()

    texts = [c.text for c in new_chunks]
    ids = [c.chunk_id for c in new_chunks]
    metas = [flatten_metadata(c.metadata) for c in new_chunks]
    vectors = embed_texts(
        embedder, texts, batch_size=batch_size, show_progress=show_progress,
        desc=f"向量化[kb{kb_id}/{name}]",
    )
    if not skip_health_check:
        check_embedding_health(vectors)

    bs = batch_size or settings.embedding.batch_size
    for start in range(0, len(ids), bs):
        end = start + bs
        collection.upsert(
            ids=ids[start:end],
            documents=texts[start:end],
            embeddings=[v.tolist() for v in vectors[start:end]],
            metadatas=metas[start:end],
        )

    # BM25：读回旧索引后追加；不存在则新建
    target = bm25_path_for_kb(kb_id, name)
    if target.exists():
        index = load_bm25_index_kb(kb_id, name)
    else:
        index = BM25Index(strategy=name, meta={"format_version": BM25_FORMAT_VERSION, "strategy": name})

    new_tokens = tokenize_corpus(texts, show_progress=show_progress)
    index.documents.extend(
        {"text": c.text, "metadata": dict(c.metadata)} for c in new_chunks
    )
    index.tokenized.extend(new_tokens)
    index.strategy = name
    index.meta = {
        **index.meta,
        "tokenizer": settings.retrieval.bm25_tokenizer,
        "k1": settings.retrieval.bm25_k1,
        "b": settings.retrieval.bm25_b,
        "count": len(index.documents),
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_bm25_index(index, target)

    return {
        "kb_id": int(kb_id),
        "strategy": name,
        "added": len(new_chunks),
        "vector_count": collection.count(),
        "bm25_count": len(index.documents),
    }


def delete_kb_index(kb_id: int, strategy: str | None = None) -> dict[str, Any]:
    """删除知识库的全部索引：所有 ``kb_<kb_id>_*`` 集合与 ``kb_<kb_id>.pkl``。"""
    deleted: dict[str, Any] = {"kb_id": int(kb_id), "collections": [], "bm25": False}
    prefix = f"kb_{int(kb_id)}_"

    try:
        client = get_chroma_client()
        for coll in client.list_collections():
            name = getattr(coll, "name", None) or str(coll)
            if name.startswith(prefix):
                try:
                    client.delete_collection(name)
                    deleted["collections"].append(name)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001 - 向量库不可用时仍继续清理 BM25
        pass

    bp = bm25_path_for_kb(kb_id, strategy)
    if bp.exists():
        bp.unlink()
        deleted["bm25"] = True
    return deleted


def index_stats(kb_id: int, strategy: str | None = None) -> dict[str, Any]:
    """查看知识库的索引现状（不重建），供 API 与前端展示。"""
    name = (strategy or settings.chunk.default_strategy).strip().lower()
    info: dict[str, Any] = {
        "kb_id": int(kb_id), "strategy": name, "chunks_file": None, "chunks": 0,
        "collection": None, "vector_count": 0, "bm25_file": None, "bm25_count": 0,
    }

    cf = chunks_file(kb_id, name)
    if cf.exists():
        info["chunks_file"] = cf.name
        try:
            info["chunks"] = len(json.loads(cf.read_text(encoding="utf-8")).get("chunks", []))
        except Exception:  # noqa: BLE001
            pass

    coll = collection_name_for_kb(kb_id, name)
    try:
        client = get_chroma_client()
        info["collection"] = coll
        info["vector_count"] = client.get_collection(coll).count()
    except Exception:  # noqa: BLE001 - 集合不存在
        info["collection"] = coll

    bp = bm25_path_for_kb(kb_id, name)
    if bp.exists():
        info["bm25_file"] = bp.name
        try:
            info["bm25_count"] = len(load_bm25_index_kb(kb_id, name))
        except Exception:  # noqa: BLE001
            pass
    return info
