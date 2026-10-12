"""阶段 0 环境冒烟测试。

逐层验证：配置 -> 第三方库导入 -> LangChain 切分 -> Chroma 读写 -> BM25 -> jieba -> torch。

运行：.venv\\Scripts\\python.exe scripts\\smoke_test.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str):
    """装饰器：记录每个检查项的成败，异常不中断整体测试。"""

    def deco(fn):
        try:
            detail = fn() or ""
            results.append((name, PASS, str(detail)))
        except Exception as exc:  # noqa: BLE001
            results.append((name, FAIL, f"{type(exc).__name__}: {exc}"))
            traceback.print_exc(limit=3)
        return fn

    return deco

@check("配置 config.settings 加载")
def _cfg():
    from config import settings

    assert settings.retrieval.top_k == 20
    assert settings.rerank.backend == "cross_encoder"
    assert settings.paths.chroma_dir.exists(), "chroma 目录未创建"
    return settings.summary()[:90]

@check("导入 langchain / langchain_core / community")
def _import_lc():
    import langchain
    import langchain_community
    import langchain_core

    return f"langchain={langchain.__version__} core={langchain_core.__version__}"


@check("导入 langchain_chroma / chromadb（pydantic v2 兼容性关键点）")
def _import_chroma():
    import chromadb
    import langchain_chroma

    return f"chromadb={chromadb.__version__} langchain_chroma OK"


@check("导入 langchain_huggingface.HuggingFaceEmbeddings")
def _import_hf():
    from langchain_huggingface import HuggingFaceEmbeddings  # noqa: F401

    return "HuggingFaceEmbeddings 可用"


@check("导入 sentence_transformers 与 CrossEncoder（Rerank 后端）")
def _import_st():
    import sentence_transformers as st
    from sentence_transformers import CrossEncoder  # noqa: F401

    return f"sentence-transformers={st.__version__} CrossEncoder 可用"


@check("导入 transformers / torch")
def _import_torch():
    import torch
    import transformers

    cuda = torch.cuda.is_available()
    dev = torch.cuda.get_device_name(0) if cuda else "CPU only"
    return f"torch={torch.__version__} transformers={transformers.__version__} device={dev}"


@check("导入 rank_bm25")
def _import_bm25():
    from rank_bm25 import BM25Okapi

    return "BM25Okapi 可用"


@check("导入 openai / fastapi / streamlit")
def _import_svc():
    import fastapi
    import openai
    import streamlit

    return f"openai={openai.__version__} fastapi={fastapi.__version__} streamlit={streamlit.__version__}"


@check("导入 pypdf / pandas / jieba")
def _import_misc():
    import jieba
    import pandas
    import pypdf

    return f"pypdf={pypdf.__version__} pandas={pandas.__version__} jieba OK"

@check("jieba 中文分词")
def _jieba():
    import jieba

    tokens = [t for t in jieba.lcut("混合检索与Rerank的RAG领域问答系统") if t.strip()]
    assert len(tokens) >= 5, tokens
    return " ".join(tokens)


@check("LangChain 文本切分（RecursiveCharacterTextSplitter）")
def _split():
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from config import settings

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk.chunk_size,
        chunk_overlap=settings.chunk.chunk_overlap,
        separators=settings.chunk.separators,
    )
    text = "检索增强生成（RAG）通过引入外部知识库提升大模型回答的准确性。" * 40
    chunks = splitter.split_text(text)
    assert len(chunks) > 1, f"未切分出多块: {len(chunks)}"
    return f"{len(text)} 字 -> {len(chunks)} 块，首块 {len(chunks[0])} 字"


@check("Chroma 集合创建与增删查（写入/持久化/检索）")
def _chroma():
    import shutil

    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from langchain_chroma import Chroma

    from config import configure_chroma_telemetry

    configure_chroma_telemetry()
    # 探针目录放在系统临时目录，避免污染项目 indexes/（Chroma 在 Windows 上
    # 即使用完自删也常留下空目录）
    import tempfile

    probe_dir = Path(tempfile.mkdtemp(prefix="rag_smoke_chroma_"))
    if probe_dir.exists():
        shutil.rmtree(probe_dir, ignore_errors=True)

    class FakeEmbeddings(Embeddings):
        """
        确定性向量：按字符做哈希 one-hot 后归一化。

        实现真实 Embeddings 接口（而非鸭子类型），保证与生产代码同一条链路。
        不能用 hash(整句)：那样任意两句话的向量距离都相同，检索会退化成随机。
        """

        dim = 1024

        def _vec(self, text: str) -> list[float]:
            vec = [0.0] * self.dim
            for ch in text:
                vec[hash(ch) % self.dim] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            return [v / norm for v in vec]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [self._vec(t) for t in texts]

        def embed_query(self, text: str) -> list[float]:
            return self._vec(text)

    store = Chroma(
        collection_name="smoke_probe",
        embedding_function=FakeEmbeddings(),
        persist_directory=str(probe_dir),
    )
    store.add_documents(
        [
            Document(
                page_content="混合检索结合稠密向量检索与稀疏检索",
                metadata={"source": "a.pdf", "page": 1},
            ),
            Document(
                page_content="精排阶段使用交叉编码器对候选文档重新打分",
                metadata={"source": "b.pdf", "page": 2},
            ),
        ]
    )
    assert store._collection.count() == 2, store._collection.count()

    # 重开一次，验证持久化确实落盘
    store2 = Chroma(
        collection_name="smoke_probe",
        embedding_function=FakeEmbeddings(),
        persist_directory=str(probe_dir),
    )
    n = store2._collection.count()
    assert n == 2, f"持久化失败，重开后条数={n}"
    hits = store2.similarity_search("交叉编码器精排", k=1)
    assert hits, "检索无结果"
    assert hits[0].metadata["source"] == "b.pdf", f"期望命中 b.pdf，实际 {hits[0].metadata}"

    # 自清理：把探针集合与目录一并删除，避免污染 indexes/ 目录
    store2.delete_collection()
    del store, store2
    import gc

    gc.collect()
    shutil.rmtree(probe_dir, ignore_errors=True)
    return f"写入 2 条 -> 重开仍为 {n} 条，检索「交叉编码器精排」命中 {hits[0].metadata['source']}"


@check("BM25 索引与检索")
def _bm25():
    import jieba
    from rank_bm25 import BM25Okapi

    corpus = ["混合检索结合稠密与稀疏检索", "交叉编码器用于精排重排序", "大模型存在幻觉问题"]
    tokenized = [list(jieba.lcut(d)) for d in corpus]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(list(jieba.lcut("精排重排序")))
    top = int(max(range(len(scores)), key=lambda i: scores[i]))
    assert top == 1, f"期望命中下标 1，实际 {top}, scores={scores}"
    return f"命中「{corpus[top]}」score={scores[top]:.3f}"


@check("FastAPI 应用可实例化")
def _fastapi():
    from fastapi import FastAPI

    app = FastAPI()
    routes = [r.path for r in app.routes]
    return f"路由数={len(routes)}"


def main() -> int:
    print("=" * 84)
    print(" 阶段 0 环境冒烟测试")
    print("=" * 84)
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, status, detail in results:
        icon = "[OK]  " if status == PASS else "[FAIL]"
        if status == FAIL:
            failed += 1
        print(f"{icon} {name:<{width}}  {detail}")
    print("-" * 84)
    print(f" 合计 {len(results)} 项，通过 {len(results) - failed} 项，失败 {failed} 项")
    print("=" * 84)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
