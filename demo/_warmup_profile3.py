"""临时脚本：细粒度拆分 load_embedder 内部耗时（用完即删）。"""
import os
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from config import settings  # noqa: E402


def tick(label, t0):
    print(f"  {label}: {time.perf_counter() - t0:.2f}s", flush=True)


t = time.perf_counter()
import torch  # noqa: E402
tick("import torch", t)

t = time.perf_counter()
print("  cuda.is_available() =", torch.cuda.is_available())
tick("torch.cuda.is_available()", t)

t = time.perf_counter()
torch.set_num_threads(os.cpu_count() or torch.get_num_threads())
tick(f"set_num_threads({torch.get_num_threads()})", t)

t = time.perf_counter()
from src.ingest.indexer import resolve_local_snapshot  # noqa: E402
tick("import indexer.resolve_local_snapshot", t)

t = time.perf_counter()
local = resolve_local_snapshot(settings.embedding.resolved_model)
tick(f"resolve_local_snapshot -> {local}", t)

t = time.perf_counter()
from transformers import AutoModel, AutoTokenizer  # noqa: E402
tick("import transformers(AutoModel/AutoTokenizer)", t)

t = time.perf_counter()
tok = AutoTokenizer.from_pretrained(str(local))
tick("AutoTokenizer.from_pretrained", t)

t = time.perf_counter()
model = AutoModel.from_pretrained(str(local)).eval()
tick("AutoModel.from_pretrained", t)

t = time.perf_counter()
with torch.inference_mode():
    out = model(**tok(["测试文本"], return_tensors="pt")).last_hidden_state[:, 0, :]
tick("首次前向", t)
print("  向量维度:", tuple(out.shape))
