"""真实模型加载验证（需已下载 BGE 模型）。

1. bge-large-zh-v1.5：可加载、输出 1024 维，语义相近句余弦相似度更高
2. query 加检索指令前缀后相似度确有提升（bge 官方推荐用法）
3. bge-reranker-v2-m3：可通过 CrossEncoder 加载并对 (query, doc) 打分
4. 精排能把真正相关的文档排在前面

运行：``.venv\\Scripts\\python.exe scripts\\verify_models.py``
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb)


def main() -> int:
    cfg = settings
    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = "cuda"
    except Exception:  # noqa: BLE001
        pass

    print("=" * 84)
    print(" 真实模型加载验证")
    print("=" * 84)
    print(f"device      : {device}")
    print(f"HF_HOME     : {__import__('os').environ.get('HF_HOME', '(未设置)')}")
    print("-" * 84)

    failures: list[str] = []
    print(f"[1/2] 加载 Embedding: {cfg.embedding.resolved_model}")
    t0 = time.time()
    try:
        from sentence_transformers import SentenceTransformer

        embedder = SentenceTransformer(cfg.embedding.resolved_model, device=device)
        dim = embedder.get_sentence_embedding_dimension()
        print(f"      加载耗时 {time.time() - t0:.1f}s，维度 = {dim}")
        if dim != cfg.embedding.dimension:
            failures.append(
                f"模型维度 {dim} 与配置 embedding.dimension={cfg.embedding.dimension} 不一致！"
                "必须修正 config，否则 Chroma 集合会维度冲突"
            )
            print(f"      [FAIL] 维度与配置不符（配置为 {cfg.embedding.dimension}）")
        else:
            print(f"      [OK] 维度与配置 embedding.dimension={cfg.embedding.dimension} 一致")

        query = "混合检索中的Rerank有什么作用？"
        docs = [
            "Rerank 使用交叉编码器对候选文档进行精排，显著提升最终上下文的相关性。",
            "今天天气晴朗，适合外出散步和野餐。",
            "BM25 是一种基于词频与逆文档频率的经典稀疏检索算法。",
        ]
        doc_vecs = embedder.encode(docs, normalize_embeddings=True, convert_to_numpy=True)

        q_raw = embedder.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0]
        q_ins = embedder.encode(
            [cfg.embedding.query_instruction + query],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )[0]

        print("      各文档与 query 的余弦相似度：")
        raw_scores = [cosine(q_raw, d) for d in doc_vecs]
        ins_scores = [cosine(q_ins, d) for d in doc_vecs]
        for i, d in enumerate(docs):
            print(f"        [原始] {raw_scores[i]:.4f}   [加指令] {ins_scores[i]:.4f}   {d[:26]}...")

        best = max(range(len(docs)), key=lambda i: raw_scores[i])
        if best != 0:
            failures.append(f"语义检索未把最相关文档排在第一，实际第一是第 {best + 1} 条")
            print("      [FAIL] 最相关文档未被排到第一")
        else:
            print("      [OK] 最相关文档（Rerank 说明）相似度最高")
        print(f"      指令前缀效果: 相关文档 {raw_scores[0]:.4f} -> {ins_scores[0]:.4f} "
              f"({ins_scores[0] - raw_scores[0]:+.4f})")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"Embedding 加载失败: {type(exc).__name__}: {exc}")
        print(f"      [FAIL] {type(exc).__name__}: {exc}")

    print("-" * 84)
    backend = cfg.rerank.backend
    print(f"[2/2] 加载 Rerank: {cfg.rerank.resolved_model}  (backend={backend})")
    t0 = time.time()
    try:
        query = "为什么需要 Rerank 精排？"
        docs = [
            "BM25 通过词频统计进行检索，计算速度快但无法理解语义。",
            "向量检索可能出现语义漂移，Rerank 通过交叉编码器对候选重新打分，提高排序质量。",
            "本系统的前端使用 Streamlit 实现。",
        ]
        if backend == "cross_encoder":
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(cfg.rerank.resolved_model, device=device)
            scores = model.predict([(query, d) for d in docs], batch_size=cfg.rerank.batch_size)
        else:
            from FlagEmbedding import FlagReranker

            model = FlagReranker(cfg.rerank.resolved_model, use_fp16=(device == "cuda"))
            scores = model.compute_score([(query, d) for d in docs])

        print(f"      加载+打分耗时 {time.time() - t0:.1f}s")
        scores = [float(s) for s in scores]
        for d, s in zip(docs, scores):
            print(f"        score={s:+.4f}   {d[:34]}...")
        best = max(range(len(docs)), key=lambda i: scores[i])
        if best != 1:
            failures.append(f"Rerank 未把最相关文档排第一，实际第 {best + 1} 条")
            print("      [FAIL] 最相关文档未被排到第一")
        else:
            print("      [OK] 精排将最相关文档（Rerank 说明）排在第一位")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"Rerank 加载失败: {type(exc).__name__}: {exc}")
        print(f"      [FAIL] {type(exc).__name__}: {exc}")

    print("=" * 84)
    if failures:
        print(" 结论：存在失败项")
        for f in failures:
            print(f"   - {f}")
        print("=" * 84)
        return 1
    print(" 结论：Embedding 与 Rerank 均验证通过 ✅")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
