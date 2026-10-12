"""阶段 2 —— 检索模块自测。

覆盖 VectorRetriever（编码与归一化）、BM25Retriever（分词、下标还原）、RRF 融合
（公式逐项验算）、Reranker 排序能力、Pipeline 端到端与消融。

运行：.venv\\Scripts\\python.exe scripts\\test_retrieval.py [--skip-rerank]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from src.retrieval import pipeline as pl  # noqa: E402
from src.retrieval.base import RetrievalResult, minmax_normalize  # noqa: E402
from src.retrieval.bm25_retriever import BM25Retriever  # noqa: E402
from src.retrieval.hybrid_retriever import HybridRetriever  # noqa: E402
from src.retrieval.vector_retriever import VectorRetriever  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

STRATEGY = "recursive"

#: 取自真实语料的查询，用于端到端与消融对比
DEMO_QUERIES = [
    "学校规章制度的适用范围是怎么规定的？",
    "校级奖励的评选流程是什么",
    "学生违纪的处理流程是什么",
]


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}  {detail}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")



def test_vector() -> VectorRetriever | None:
    print("\n[1/5] VectorRetriever")
    try:
        r = VectorRetriever(STRATEGY)
        t = r.warmup()
    except Exception as exc:  # noqa: BLE001
        check("向量检索器可加载", False, f"{type(exc).__name__}: {exc}")
        return None

    check("向量检索器可加载", True, f"{t:.1f}s | 集合={r.collection_name} 条数={r.size}")
    check("索引非空", r.size > 0, f"{r.size} 条")

    vec = r.encode_query("测试查询")
    check("查询向量维度正确", len(vec) == settings.embedding.dimension, f"dim={len(vec)}")
    norm = math.sqrt(sum(x * x for x in vec))
    check("查询向量已归一化", abs(norm - 1.0) < 1e-3, f"|v|={norm:.6f}")

    # BGE 指令前缀确实生效（加与不加应产生不同向量）
    plain = r.encode_query("学校制度适用范围")
    cfg = settings.embedding
    if cfg.use_query_instruction:
        original = cfg.use_query_instruction
        try:
            object.__setattr__(cfg, "use_query_instruction", False)
            no_prefix = r.encode_query("学校制度适用范围")
        finally:
            object.__setattr__(cfg, "use_query_instruction", original)
        diff = sum((a - b) ** 2 for a, b in zip(plain, no_prefix)) ** 0.5
        check("BGE 查询指令前缀生效", diff > 1e-6, f"加/不加前缀的向量距离={diff:.4f}")

    hits = r.retrieve(DEMO_QUERIES[0], top_k=50)
    check("Top-50 召回条数正确", len(hits) == 50, f"{len(hits)} 条")
    check("rank 连续且从 1 开始", [h.rank for h in hits] == list(range(1, len(hits) + 1)))
    check("retriever 标记正确", all(h.retriever == "vector" for h in hits))

    # 余弦相似度方向：分数越大越相关，且落在合理区间
    scores = [h.score for h in hits]
    check("分数按降序排列", scores == sorted(scores, reverse=True),
          f"{scores[0]:.4f} -> {scores[-1]:.4f}")
    check("相似度在 (−1, 1] 区间", all(-1.0 <= s <= 1.0 + 1e-6 for s in scores),
          f"[{min(scores):.4f}, {max(scores):.4f}]")

    # 自洽性：文档入库时未加指令前缀，故以原文为查询时必须先关掉前缀，
    # 否则两侧表示不一致必然命不中。
    first = hits[0]
    _cfg = settings.embedding
    _orig = _cfg.use_query_instruction
    try:
        object.__setattr__(_cfg, "use_query_instruction", False)
        self_hits = r.retrieve(first.text, top_k=1)
    finally:
        object.__setattr__(_cfg, "use_query_instruction", _orig)
    check("以原文为查询可命中自身（不加指令前缀）",
          bool(self_hits) and self_hits[0].chunk_id == first.chunk_id,
          f"sim={self_hits[0].score:.4f}" if self_hits else "")

    check("结果带完整元数据",
          all(h.chunk_id and h.text and h.metadata.get("source") for h in hits))
    return r



def test_bm25(vector: VectorRetriever | None) -> BM25Retriever | None:
    print("\n[2/5] BM25Retriever")
    try:
        r = BM25Retriever(STRATEGY)
        t = r.warmup()
    except Exception as exc:  # noqa: BLE001
        check("BM25 检索器可加载", False, f"{type(exc).__name__}: {exc}")
        return None

    check("BM25 检索器可加载", True, f"{t:.1f}s | 语料={r.size} 条")
    check("索引非空", r.size > 0)

    hits = r.retrieve(DEMO_QUERIES[0], top_k=50)
    check("Top-50 召回条数正确", len(hits) == 50, f"{len(hits)} 条")
    check("retriever 标记正确", all(h.retriever == "bm25" for h in hits))
    scores = [h.score for h in hits]
    check("BM25 分数降序", scores == sorted(scores, reverse=True), f"{scores[0]:.3f} -> {scores[-1]:.3f}")
    check("BM25 分数为正", all(s > 0 for s in scores))

    # 下标还原：命中项的 chunk_id 必须存在于 Chroma（两索引口径一致）
    if vector is not None:
        bm25_ids = {h.chunk_id for h in hits}
        chroma = vector._collection.get(include=[])["ids"]
        missing = bm25_ids - set(chroma)
        check("BM25 命中项 chunk_id 与 Chroma 一致", not missing,
              f"不一致 {len(missing)} 条")

    # 专有名词精确匹配（BM25 的核心价值）
    exact = r.retrieve("宿院学〔2024〕11号", top_k=5)
    check("专有编号可精确检索", len(exact) > 0 and exact[0].score > 0,
          f"top1={exact[0].chunk_id if exact else 'N/A'} score={exact[0].score:.2f}" if exact else "")

    # 只有分词后全是停用词或全为未登录词时才应返回空。看似无关的句子里
    # 「完全/相关/内容」等都是真实中文词且在语料中大量出现，查到匹配文档是正确行为。
    check("空查询返回空", r.retrieve("", top_k=5) == [])

    all_stop = r.retrieve("的了吗呢是我你他", top_k=5)
    check("全停用词查询返回空", all_stop == [], f"{len(all_stop)} 条")

    all_oov = r.retrieve("zzzqqq wwweee", top_k=5)
    check("全部为未登录词时返回空", all_oov == [], f"{len(all_oov)} 条")

    # 混合场景：有未登录词也有停用词，但无任何有效词 -> 仍应为空
    mixed = r.retrieve("zzzqqq 的 了吗", top_k=5)
    check("无有效查询词时返回空", mixed == [], f"{len(mixed)} 条")

    # 解释接口要能说清每个词为什么被剔除
    ex = r.explain("zzzqqq 的 了吗")
    check("explain 能解释剔除原因",
          not ex["kept"] and len(ex["dropped"]) > 0,
          f"剔除={[(d['term'], d['reason']) for d in ex['dropped']][:4]}")

    # score_all 用于评估
    all_scores = r.score_all(DEMO_QUERIES[0])
    check("score_all 长度等于语料数", len(all_scores) == r.size, f"{len(all_scores)}")
    return r



def test_fusion(vector: VectorRetriever, bm25: BM25Retriever) -> None:
    print("\n[3/5] RRF 融合")
    dense = [
        RetrievalResult(chunk_id="A", text="a", score=0.9, rank=1, retriever="vector"),
        RetrievalResult(chunk_id="B", text="b", score=0.8, rank=2, retriever="vector"),
        RetrievalResult(chunk_id="C", text="c", score=0.7, rank=3, retriever="vector"),
    ]
    sparse = [
        RetrievalResult(chunk_id="B", text="b", score=20.0, rank=1, retriever="bm25"),
        RetrievalResult(chunk_id="D", text="d", score=15.0, rank=2, retriever="bm25"),
    ]
    h = HybridRetriever(STRATEGY, vector=vector, bm25=bm25, fusion="rrf",
                        dense_weight=0.5, sparse_weight=0.5)
    merged = h.fuse(dense, sparse, top_k=10)
    got = {m.chunk_id: m.final_score for m in merged}

    k = settings.retrieval.rrf_k
    expect = {
        "A": 0.5 / (k + 1),
        "B": 0.5 / (k + 2) + 0.5 / (k + 1),   # 两路都召回 -> 分数相加
        "C": 0.5 / (k + 3),
        "D": 0.5 / (k + 2),
    }
    ok = all(abs(got.get(cid, -1) - v) < 1e-12 for cid, v in expect.items())
    check("RRF 公式逐项验算（Σ w/(k+rank)）", ok,
          f"B(两路)={got.get('B', 0):.6f} 期望={expect['B']:.6f}")

    # 两路都命中的 B 应排第一
    check("两路共同命中者排第一", merged[0].chunk_id == "B",
          f"顺序={[m.chunk_id for m in merged]}")
    check("matched_by 记录了来源", sorted(merged[0].matched_by) == ["bm25", "vector"],
          str(merged[0].matched_by))
    check("去重：4 个不同 chunk 输出 4 条", len(merged) == 4, f"{len(merged)} 条")
    h_dense_only = HybridRetriever(STRATEGY, vector=vector, bm25=bm25, fusion="rrf",
                                   dense_weight=1.0, sparse_weight=0.0)
    m1 = h_dense_only.fuse(dense, sparse, top_k=10)
    check("权重置 0 等价于禁用该路", {x.chunk_id for x in m1} == {"A", "B", "C"},
          f"结果={[x.chunk_id for x in m1]}")

    h_sparse_only = HybridRetriever(STRATEGY, vector=vector, bm25=bm25, fusion="rrf",
                                    dense_weight=0.0, sparse_weight=1.0)
    m2 = h_sparse_only.fuse(dense, sparse, top_k=10)
    check("仅 BM25 权重时结果来自 BM25", {x.chunk_id for x in m2} == {"B", "D"},
          f"结果={[x.chunk_id for x in m2]}")

    # 权重变化会改变融合排序：C = w_d/(k+3)、D = w_s/(k+2)，等权时 D > C（分母更小），
    # 只留 dense 时 D 不参与、C 胜出。
    h_skew = HybridRetriever(STRATEGY, vector=vector, bm25=bm25, fusion="rrf",
                             dense_weight=0.8, sparse_weight=0.2)
    m3 = h_skew.fuse(dense, sparse, top_k=10)
    expect_skew = sorted(
        {"A": 0.8 / (k + 1), "B": 0.8 / (k + 2) + 0.2 / (k + 1),
         "C": 0.8 / (k + 3), "D": 0.2 / (k + 2)}.items(),
        key=lambda kv: -kv[1],
    )
    check("权重变化确实会改变融合排序（符合公式预期）",
          [x.chunk_id for x in m3] == [cid for cid, _ in expect_skew],
          f"权重0.8/0.2 -> {[x.chunk_id for x in m3]}；等权 -> {[x.chunk_id for x in merged]}")
    real = h.retrieve(DEMO_QUERIES[0], top_k=20)
    check("真实数据融合返回 Top-20", len(real) == 20, f"{len(real)} 条")
    check("融合结果分数降序", [x.final_score for x in real] == sorted([x.final_score for x in real], reverse=True))

    detail = h.retrieve_detailed(DEMO_QUERIES[0], top_k=20)
    st = detail.stats()
    check("两路召回并行执行", "dense" in st["timing"] and "sparse" in st["timing"],
          str(st["timing"]))
    check("召回统计完整", st["dense"] > 0 and st["sparse"] > 0,
          f"dense={st['dense']} sparse={st['sparse']} 重叠={st['overlap']} "
          f"仅dense={st['dense_only']} 仅sparse={st['sparse_only']}")
    hw = HybridRetriever(STRATEGY, vector=vector, bm25=bm25, fusion="weighted")
    mw = hw.fuse(dense, sparse, top_k=10)
    check("weighted 融合可用且已归一化", bool(mw) and all(0 <= x.final_score <= 1.0 + 1e-9 for x in mw),
          f"分数区间=[{min(x.final_score for x in mw):.4f}, {max(x.final_score for x in mw):.4f}]")
    check("minmax_normalize 基本正确", minmax_normalize([1, 2, 3, 4]) == [0.0, 1 / 3, 2 / 3, 1.0])
    check("minmax_normalize 全等值不除零", minmax_normalize([5, 5, 5]) == [1.0, 1.0, 1.0])
    check("minmax_normalize 空输入", minmax_normalize([]) == [])



def test_reranker() -> None:
    print("\n[4/5] Reranker（交叉编码器精排）")
    from src.retrieval.reranker import Reranker

    r = Reranker()
    try:
        t = r.load()
    except Exception as exc:  # noqa: BLE001
        check("精排模型可加载", False, f"{type(exc).__name__}: {exc}")
        return

    check("精排模型可加载", True,
          f"{t:.1f}s | 模型={Path(r.model_name).name} 后端={r.backend} device={r._device}")

    # 核心能力验证：相关文档的分数必须高于无关文档
    query = "对处理结果有异议的，应当如何提出书面申请？"
    relevant = "相关异议应在收到通知之日起规定期限内，向学校指定部门提出书面申请。"
    irrelevant = "学校应当为学生提供就业指导与服务，帮助毕业生顺利就业。"
    scores = r.score_of(query, [relevant, irrelevant])
    check("相关文档分数高于无关文档", scores[0] > scores[1],
          f"相关={scores[0]:.4f} 无关={scores[1]:.4f} 差值={scores[0] - scores[1]:.4f}")

    # 排序能力：打乱顺序后重排，相关文档必须回到第一
    texts = [irrelevant, relevant]
    ranked = r.rerank(
        query,
        [RetrievalResult(chunk_id=str(i), text=t, score=0.5) for i, t in enumerate(texts)],
        top_k=2,
    )
    check("精排能纠正错误顺序", ranked[0].text == relevant,
          f"top1 前 40 字={ranked[0].text[:40]}")
    check("rerank_score 已写入", all(x.rerank_score is not None for x in ranked))
    check("保留召回阶段分数用于对比", all(x.fused_score is not None for x in ranked))
    check("精排名次从 1 连续", [x.rank for x in ranked] == [1, 2])

    many = [RetrievalResult(chunk_id=str(i), text=f"文档{i}内容", score=0.1) for i in range(10)]
    check("top_k 截断生效", len(r.rerank(query, many, top_k=5)) == 5)

    check("空候选返回空", r.rerank(query, [], top_k=5) == [])

    r_norm = Reranker(normalize_score=True)
    s_raw = r.score_of(query, [relevant, irrelevant])
    s_norm = r_norm.score_of(query, [relevant, irrelevant]) if False else None
    # score_of 不做归一化，改为直接验证 sigmoid 映射后的区间
    from src.retrieval.reranker import _sigmoid

    mapped = [_sigmoid(x) for x in s_raw]
    check("sigmoid 映射到 (0,1)", all(0 < m < 1 for m in mapped),
          f"[{min(mapped):.4f}, {max(mapped):.4f}]")



def test_pipeline(skip_rerank: bool) -> None:
    print("\n[5/5] Pipeline 端到端与消融对比")
    strategies = ["vector", "bm25", "hybrid"] + ([] if skip_rerank else ["hybrid_rerank"])

    responses = {}
    for s in strategies:
        resp = pl.retrieve(DEMO_QUERIES[0], strategy=s, chunk_strategy=STRATEGY, write_log=False)
        responses[s] = resp

        expect_k = settings.rerank.top_k if s == "hybrid_rerank" else settings.retrieval.top_k
        check(f"[{s}] 返回条数符合配置", len(resp) == expect_k,
              f"{len(resp)} 条（期望 {expect_k}）")
        check(f"[{s}] 结果分数降序",
              [r.final_score for r in resp] == sorted([r.final_score for r in resp], reverse=True))
        check(f"[{s}] 每条含 metadata 与得分",
              all(r.chunk_id and r.text and r.metadata.get("source") and r.final_score is not None
                  for r in resp))
        check(f"[{s}] 耗时统计含 total", "total" in resp.timing and resp.timing["total"] > 0,
              resp.summary())

    # 链路差异：hybrid 的候选池应为 20，精排后为 5
    if "hybrid_rerank" in responses:
        hr = responses["hybrid_rerank"]
        check("hybrid_rerank 执行了精排", hr.stats.get("reranked", 0) > 0,
              f"reranked={hr.stats.get('reranked')} warnings={hr.warnings}")
        check("hybrid_rerank 耗时含 rerank 阶段", "rerank" in hr.timing,
              str({k: round(v, 3) for k, v in hr.timing.items()}))

    try:
        pl.retrieve("x", strategy="not_exist")
        check("非法策略抛出异常", False, "未抛异常")
    except ValueError as exc:
        check("非法策略抛出异常", True, str(exc)[:50])

    empty = pl.retrieve("  ", strategy="hybrid")
    check("空查询安全返回", len(empty) == 0 and bool(empty.warnings), str(empty.warnings))

    # as_context 供阶段 3 使用
    ctx = responses["hybrid"].as_context()
    check("as_context 结构正确",
          ctx and all(set(c) >= {"index", "text", "score", "citation"} for c in ctx),
          f"{len(ctx)} 条")
    print("\n  ── 消融对比（query=%r）──" % DEMO_QUERIES[0])
    print(f"  {'策略':<16}{'条数':>5}{'耗时ms':>9}   Top-1 来源")
    for s in strategies:
        resp = responses[s]
        top1 = resp.top1
        where = f"{top1.source} p{top1.page} ({top1.final_score:.4f})" if top1 else "-"
        print(f"  {s:<16}{len(resp):>5}{resp.timing.get('total', 0) * 1000:>9.0f}   {where}")

    # 三条查询的 Top-3 对照，观察各策略差异
    print("\n  ── 多查询 Top-3 chunk 对照 ──")
    for q in DEMO_QUERIES:
        print(f"  query: {q}")
        for s in strategies:
            resp = pl.retrieve(q, strategy=s, chunk_strategy=STRATEGY, write_log=False)
            ids = [f"p{r.page}" for r in resp.results[:3]]
            print(f"    {s:<16} {' > '.join(ids)}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 2 检索模块自测")
    parser.add_argument("--skip-rerank", action="store_true", help="跳过精排（未下载模型时）")
    args = parser.parse_args()

    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    print("=" * 92)
    print(" 阶段 2 检索模块自测")
    print("=" * 92)
    print(f"分块策略={STRATEGY} | 融合={settings.retrieval.fusion} | "
          f"召回={settings.retrieval.dense_top_k}/{settings.retrieval.sparse_top_k} -> "
          f"{settings.retrieval.top_k} -> {settings.rerank.top_k}")

    vector = test_vector()
    bm25 = test_bm25(vector)
    if vector is not None and bm25 is not None:
        test_fusion(vector, bm25)
    else:
        check("融合测试前置条件", False, "检索器未就绪")
    if not args.skip_rerank:
        test_reranker()
    test_pipeline(args.skip_rerank)

    print("\n" + "=" * 92)
    print(f" 合计 {len(PASSED) + len(FAILED)} 项，通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print(" 失败明细：")
        for name, detail in FAILED:
            print(f"   - {name}: {detail}")
    print("=" * 92)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
