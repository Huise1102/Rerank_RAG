"""临时脚本：定位预热各阶段耗时（用完即删）。"""
import gc
import os
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def tick(label, t0):
    print(f"  {label}: {time.perf_counter() - t0:.2f}s", flush=True)


print("[1] 模块导入（真实顺序：chromadb -> torch -> transformers）", flush=True)
t = time.perf_counter()
import chromadb  # noqa: E402
tick("chromadb", t)
t = time.perf_counter()
import torch  # noqa: E402
tick("torch", t)
t = time.perf_counter()
import transformers  # noqa: E402
tick("transformers", t)
import jieba  # noqa: E402
import numpy  # noqa: E402

print("[2] Chroma 客户端 + 集合", flush=True)
from chromadb.config import Settings as ChromaSettings  # noqa: E402

t = time.perf_counter()
client = chromadb.PersistentClient(
    path=os.path.join(ROOT, "indexes", "chroma"),
    settings=ChromaSettings(anonymized_telemetry=False),
)
tick("PersistentClient", t)
t = time.perf_counter()
coll = client.get_collection("rag_domain_qa_recursive")
tick(f"get_collection(count={coll.count()})", t)

from transformers import AutoModel, AutoTokenizer  # noqa: E402

ref = "C:/Users/98429/Documents/DeskDock/library/基于混合检索与Rerank的领域知识库系统/.hf-cache/hub/models--BAAI--bge-large-zh-v1.5/snapshots/79e7739b6ab944e86d6171e44d24c997fc1e0116"

print("[3] 默认方式加载 embedder", flush=True)
t = time.perf_counter()
tok = AutoTokenizer.from_pretrained(ref)
tick("tokenizer", t)
t = time.perf_counter()
m1 = AutoModel.from_pretrained(ref).eval()
tick("AutoModel(默认)", t)
t = time.perf_counter()
_ = m1(**tok(["测试文本"], return_tensors="pt")).last_hidden_state[:, 0, :]
tick("首次前向", t)

del m1
gc.collect()

print("[4] low_cpu_mem_usage=True 加载 embedder", flush=True)
t = time.perf_counter()
try:
    m2 = AutoModel.from_pretrained(ref, low_cpu_mem_usage=True).eval()
    tick("AutoModel(low_cpu_mem_usage)", t)
    del m2
    gc.collect()
except Exception as exc:  # noqa: BLE001
    print(f"  失败: {type(exc).__name__}: {exc}", flush=True)

print(f"进程 RSS: {__import__('psutil').Process().memory_info().rss / 2**30:.2f} GB")
