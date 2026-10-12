"""阶段 1 —— 数据入库自测。

清洗逻辑对 data/test_corpus/ 自带示例 PDF（含已知页眉页脚与三种页码）断言确定性期望；
工程链路对 data/raw 真实语料只断言结构性事实（数量一致、可还原、能检索到自己）。

运行：.venv\\Scripts\\python.exe scripts\\test_ingest.py [--real-embedder]
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from config import settings  # noqa: E402
from src.ingest import indexer, loader, splitter  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

#: 自带示例 PDF 目录（含已知页眉页脚与页码）
TEST_CORPUS = PROJECT_ROOT / "data" / "test_corpus"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}  {detail}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")


class FakeEmbedder:
    """
    确定性假 embedder，接口与 SentenceTransformer.encode 兼容。

    产生的是稀疏字符哈希 one-hot 向量，与真实 BGE 稠密向量完全不同，因此用它
    构建的索引必须写到隔离目录（见 build_isolated_indexes()），绝不能落到
    indexes/chroma 与 indexes/bm25，否则真实索引会被覆盖成垃圾。
    """

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.max_seq_length = 512

    def _vec(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for ch in text:
            vec[hash(ch) % self.dim] += 1.0
        norm = float(np.linalg.norm(vec)) or 1.0
        return vec / norm

    def encode(self, texts, batch_size=16, normalize_embeddings=True, convert_to_numpy=True,
               show_progress_bar=False):  # noqa: ANN001, ANN201
        return np.vstack([self._vec(t) for t in texts])


@contextlib.contextmanager
def build_isolated_indexes():
    """
    把索引路径临时重定向到独立目录，测试结束后恢复。

    indexer 的路径全部来自 settings.paths，不改写的话假 embedder 建出的索引
    会覆盖真实索引；改写的是 settings 实例属性，退出时原样恢复。
    """
    tmp = Path(tempfile.mkdtemp(prefix="rag_ingest_test_"))
    chroma_dir = tmp / "chroma"
    bm25_dir = tmp / "bm25"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    bm25_dir.mkdir(parents=True, exist_ok=True)

    paths = settings.paths
    saved = (paths.chroma_dir, paths.bm25_dir)
    object.__setattr__(paths, "chroma_dir", chroma_dir)
    object.__setattr__(paths, "bm25_dir", bm25_dir)
    # 换目录后必须清掉 chroma 客户端缓存，否则仍会连到旧库
    try:
        yield tmp
    finally:
        object.__setattr__(paths, "chroma_dir", saved[0])
        object.__setattr__(paths, "bm25_dir", saved[1])
        shutil.rmtree(tmp, ignore_errors=True)



def test_cleaning_on_sample() -> None:
    """自带示例 PDF 的页眉页脚识别与页码剥离（唯一有确定性期望的部分）。"""
    print("\n[1/5] 清洗逻辑（示例 PDF：已知页眉/页脚/三种页码格式）")
    sample = TEST_CORPUS / "RAG技术说明_示例.pdf"
    if not sample.exists():
        check("示例 PDF 存在", False, f"缺少 {sample}")
        return

    pages, headers = loader.load_pdf(sample)
    check("示例 PDF 可解析", len(pages) > 0, f"{len(pages)} 页")

    hf_header = [h for h in headers if h.position == "header"]
    hf_footer = [h for h in headers if h.position == "footer"]
    check("识别到页眉", len(hf_header) == 1, f"{[h.line for h in hf_header]}")
    check("识别到页脚", len(hf_footer) == 1, f"{[h.line for h in hf_footer]}")
    check(
        "页眉内容正确",
        bool(hf_header) and "白皮书" in hf_header[0].line,
        hf_header[0].line if hf_header else "",
    )
    check(
        "页脚内容正确",
        bool(hf_footer) and "内部资料" in hf_footer[0].line,
        hf_footer[0].line if hf_footer else "",
    )

    # 页眉页脚若残留在正文，说明剥离只作用于页面对象而未回写文本
    leaked = [
        (pg.page, bad)
        for pg in pages
        for bad in [h.line for h in headers]
        if bad and bad in pg.text
    ]
    check("正文中无页眉页脚残留", not leaked, f"残留 {len(leaked)} 处")

    removed = [ln for pg in pages for ln in pg.removed]
    check("页码剥离：- N -", any(ln.startswith("- ") and ln.endswith(" -") for ln in removed), str(removed[:4]))
    check("页码剥离：第 N 页", any(ln.startswith("第 ") and ln.endswith(" 页") for ln in removed))
    check("页码剥离：Page N of M", any(ln.lower().startswith("page ") for ln in removed))

    # 阅读顺序与段落重组：正文不应每 30 字就断行
    body_lines = [ln for pg in pages for ln in pg.text.split("\n") if ln.strip()]
    avg_len = sum(len(ln) for ln in body_lines) / max(len(body_lines), 1)
    check("段落重组生效（非逐视觉行换行）", avg_len > 60, f"平均行长 {avg_len:.0f} 字")

    # 页码正则健全性（不依赖语料）
    print("      页码正则抽查：")
    for text, expect in [("- 12 -", True), ("第 3 页", True), ("Page 3 of 6", True),
                         ("12/20", True), ("3.14 是圆周率", False), ("第 3 章 检索", False)]:
        got = loader.is_page_number_line(text)
        check(f"  is_page_number_line({text!r})", got == expect, f"-> {got}")



def test_loader_real() -> tuple[list, object]:
    print("\n[2/5] 真实语料解析（data/raw）")
    files = loader.iter_pdf_files()
    check("发现 PDF 文件", len(files) > 0, f"{[f.name for f in files]}")
    if not files:
        return [], None

    pages, report = loader.load_pdfs()
    check("解析出页面", len(pages) > 0, report.summary())
    check("正文非空", all(pg.text.strip() for pg in pages))
    check("无解析失败文件", not report.skipped, str(report.skipped))

    # 元数据结构正确性（不依赖具体内容）
    meta = pages[0].metadata()
    check("元数据含 source", bool(meta.get("source")), str(meta.get("source")))
    check("元数据含 page 且为正整数", isinstance(meta.get("page"), int) and meta["page"] >= 1,
          str(meta.get("page")))
    check("元数据含 total_pages", bool(meta.get("total_pages")), str(meta.get("total_pages")))
    check("标题已推断", bool(pages[0].title), repr(pages[0].title))

    # 文本质量抽查：不应出现明显的粘连或截断特征
    txt = pages[0].text
    check("无连续重复标点", "。。" not in txt and "，，" not in txt)
    check("无零宽字符残留", "\u200b" not in txt)
    return pages, report



def test_splitter(pages: list) -> None:
    print("\n[3/5] 分块（两种策略）")
    if not pages:
        check("分块前置条件", False, "无页面可切分")
        return
    docs = loader.to_documents(pages)

    for strategy in ["fixed", "recursive"]:
        spec = settings.chunk.for_strategy(strategy)
        result = splitter.split_documents(docs, strategy=strategy)
        check(f"[{strategy}] 有分块产出", result.total > 0, result.summary())
        check(
            f"[{strategy}] 参数与配置一致",
            result.params["chunk_size"] == spec.chunk_size
            and result.params["chunk_overlap"] == spec.chunk_overlap,
            f"size={result.params['chunk_size']} overlap={result.params['chunk_overlap']}",
        )
        if strategy == "recursive":
            check("[recursive] 分隔符符合要求",
                  result.params["separators"] == ["\n\n", "\n", "。", " ", ""],
                  str(result.params["separators"]))

        ids = [c.chunk_id for c in result.chunks]
        check(f"[{strategy}] chunk_id 唯一", len(set(ids)) == len(ids), f"{len(ids)} 块")

        missing = [
            c.chunk_id for c in result.chunks
            if not (c.metadata.get("source") and c.metadata.get("page") and c.metadata.get("strategy"))
        ]
        check(f"[{strategy}] 元数据完整继承", not missing, f"缺失 {len(missing)} 块")

        limit = spec.max_chunk_size or 10**9
        over = [len(c) for c in result.chunks if len(c) > limit]
        check(f"[{strategy}] 无超长块", not over, f"超限 {len(over)} 块")

        # 法规类文档里存在「短但完整」的条款（如「第二十一条 本办法由学工处负责解释。」），
        # 它们不算碎片；真正要拒绝的是被切断的碎片：既不达长度、又不以句末标点收尾。
        broken = [
            c.text for c in result.chunks
            if len(c.text) < spec.min_chunk_size and c.text.strip()[-1] not in "。！？；.!?;：:"
        ]
        short_complete = [
            len(c) for c in result.chunks
            if len(c) < spec.min_chunk_size
        ]
        check(f"[{strategy}] 无被切断的碎片", not broken,
              f"{len(broken)} 个残缺块；短但完整的块 {len(short_complete)} 个"
              + (f" 示例={broken[0][:40]!r}" if broken else ""))

        total_src = sum(len(d.page_content) for d in docs)
        check(f"[{strategy}] 内容未丢失",
              result.stats()["total_chars"] >= total_src * 0.9,
              f"分块 {result.stats()['total_chars']} vs 原文 {total_src}")

        path = splitter.save_chunks(result, strategy=strategy)
        check(f"[{strategy}] 产物落盘", path.exists(), path.name)
        check(f"[{strategy}] 可回读且块数一致",
              splitter.load_chunks(strategy).total == result.total)

    check("两策略产物文件名区分",
          splitter.chunks_file("fixed").name != splitter.chunks_file("recursive").name)



def test_indexer(embedder) -> None:
    print("\n[4/5] 索引构建（向量库 + BM25）")

    # 先验证健康校验本身有效（假 embedder 的稀疏向量必须被拦下）
    from src.ingest.indexer import check_embedding_health

    try:
        check_embedding_health(embedder.encode(["测试文本" * 5, "另一段文本" * 5]))
        check("健康校验能拦下退化向量", False, "假 embedder 的稀疏向量未被拦截")
    except ValueError as exc:
        check("健康校验能拦下退化向量", "非零维度比例" in str(exc),
              str(exc).splitlines()[1].strip() if len(str(exc).splitlines()) > 1 else "")

    flat = indexer.flatten_metadata({"a": 1, "b": None, "c": ["x", "y"], "d": {"k": "v"}, "e": 1.5})
    check("flatten_metadata 过滤 None", "b" not in flat)
    check("flatten_metadata 列表转字符串", isinstance(flat.get("c"), str), flat.get("c"))
    check("flatten_metadata 保留数值", flat.get("a") == 1 and flat.get("e") == 1.5)

    n1 = indexer.collection_name_for_strategy("fixed")
    n2 = indexer.collection_name_for_strategy("recursive")
    check("集合名按策略区分", n1 != n2, f"{n1} / {n2}")
    check("集合名合法", 3 <= len(n1) <= 63 and all(ch.isalnum() or ch in "_-" for ch in n1), n1)

    for strategy in ["fixed", "recursive"]:
        # skip_health_check=True：假 embedder 必被体检拦下，这里只验证 Chroma/BM25 读写链路
        report = indexer.build_index(strategy, embedder=embedder, show_progress=False,
                                     skip_health_check=True)
        check(f"[{strategy}] 向量索引条数正确",
              report.vector["count"] == report.chunks,
              f"Chroma {report.vector['count']} vs 分块 {report.chunks}")
        check(f"[{strategy}] 向量维度为配置值",
              report.vector["dimension"] == settings.embedding.dimension,
              f"dim={report.vector['dimension']}")
        check(f"[{strategy}] BM25 已落盘", indexer.bm25_path(strategy).exists())

        idx = indexer.load_bm25_index(strategy)
        check(f"[{strategy}] BM25 文档与分词数量一致",
              len(idx.documents) == len(idx.tokenized) == report.chunks,
              f"{len(idx.documents)} / {len(idx.tokenized)}")
        check(f"[{strategy}] BM25 保存了原文可还原",
              all(d.get("text") and d.get("metadata", {}).get("chunk_id") for d in idx.documents))

        # 从语料自身取查询词（不假设语料内容），验证「命中 -> 下标还原原文」链路
        if idx.documents:
            tokens = indexer.tokenize(idx.documents[0]["text"])
            # 取最高频的 5 个 token（去单字噪声），保证查询词确实在语料中出现过
            from collections import Counter

            common = [t for t, _ in Counter(t for t in tokens if len(t) >= 2).most_common(5)]
            query = " ".join(common) if common else idx.documents[0]["text"][:20]
            hits = idx.search(query, top_k=3)
            check(f"[{strategy}] BM25 检索有命中", len(hits) > 0, f"query={query!r}")
            if hits:
                i, score = hits[0]
                doc = idx.documents[i]
                check(f"[{strategy}] 命中项可还原原文",
                      bool(doc["text"]) and score > 0,
                      f"score={score:.3f} {doc['metadata'].get('source')} p{doc['metadata'].get('page')}")

    check("两策略 BM25 文件不同", indexer.bm25_path("fixed") != indexer.bm25_path("recursive"))
    sf, sr = indexer.index_stats("fixed"), indexer.index_stats("recursive")
    check("两策略 Chroma 集合独立", sf["collection"] != sr["collection"],
          f"{sf['vector_count']} / {sr['vector_count']} 条")


def test_chroma_readback(embedder) -> None:
    print("\n[5/5] Chroma 还原链")
    for strategy in ["fixed", "recursive"]:
        col = indexer.get_chroma_client().get_collection(indexer.collection_name_for_strategy(strategy))
        got = col.get(limit=1, include=["documents", "metadatas"])
        if not got["ids"]:
            check(f"[{strategy}] 集合可读", False, "集合为空")
            continue
        meta = got["metadatas"][0]
        check(f"[{strategy}] 元数据可还原",
              bool(meta.get("chunk_id") and meta.get("source") and meta.get("page")),
              f"chunk_id={meta.get('chunk_id')}")
        vec = embedder.encode([got["documents"][0]], normalize_embeddings=True)[0]
        res = col.query(query_embeddings=[vec.tolist()], n_results=1,
                        include=["documents", "metadatas", "distances"])
        check(f"[{strategy}] 查询命中自身",
              res["ids"][0][0] == got["ids"][0],
              f"distance={res['distances'][0][0]:.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 1 数据入库自测")
    parser.add_argument("--real-embedder", action="store_true",
                        help="使用真实的 bge-large-zh-v1.5（需已下载模型）")
    args = parser.parse_args()

    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    print("=" * 88)
    print(" 阶段 1 数据入库自测")
    print("=" * 88)

    if args.real_embedder:
        print("加载真实 embedding 模型（bge-large-zh-v1.5）...")
        embedder, device = indexer.load_embedder()
        print(f"  已加载，设备 {device}")
    else:
        embedder = FakeEmbedder()
        print("使用确定性假 embedder（无需模型；加 --real-embedder 可切换真实模型）")

    test_cleaning_on_sample()
    pages, _ = test_loader_real()
    test_splitter(pages)
    # 索引部分在隔离目录里跑：假 embedder 的向量会污染真实索引
    with build_isolated_indexes() as tmp:
        print(f"\n  （索引测试写入临时目录：{tmp}）")
        test_indexer(embedder)
        test_chroma_readback(embedder)

    print("\n" + "=" * 88)
    print(f" 合计 {len(PASSED) + len(FAILED)} 项，通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print(" 失败明细：")
        for name, detail in FAILED:
            print(f"   - {name}: {detail}")
    print("=" * 88)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
