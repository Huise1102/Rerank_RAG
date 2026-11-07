"""临时脚本：按真实 _load 顺序拆分 vector 步骤耗时（用完即删）。"""
import os
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from src.retrieval.pipeline import get_pipeline  # noqa: E402

get_pipeline()  # 与真实服务一致：模块图先导入


def tick(label, t0):
    print(f"  {label}: {time.perf_counter() - t0:.2f}s", flush=True)


t = time.perf_counter()
from src.ingest.indexer import collection_name_for_strategy, get_chroma_client  # noqa: E402
tick("import src.ingest.indexer（含 chromadb 等）", t)

t = time.perf_counter()
client = get_chroma_client()
tick("get_chroma_client", t)

t = time.perf_counter()
coll = client.get_collection(collection_name_for_strategy("recursive"))
tick(f"get_collection({coll.count()})", t)

from src.ingest.indexer import load_embedder  # noqa: E402

t = time.perf_counter()
model, device = load_embedder()
tick("load_embedder（含 torch/transformers 导入）", t)

t = time.perf_counter()
model.encode(["测试"], batch_size=1, normalize_embeddings=True, convert_to_numpy=True)
tick("首次编码", t)
