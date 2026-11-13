import os
import pickle
from pathlib import Path

import jieba
import numpy as np
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from src.core.config import settings
from src.retrieval.scope import RetrievalScope, normalize_date

jieba.setLogLevel(20)

_cache: dict[int, tuple[float, list[Document], BM25Okapi, dict[str, np.ndarray]]] = {}


def index_file(kb_id: int) -> Path:
    return settings.bm25_path / f"kb_{kb_id}.pkl"


def tokenize(text: str) -> list[str]:
    tokens = [item.strip().lower() for item in jieba.cut_for_search(text or "")]
    tokens = [item for item in tokens if item]
    return tokens or list((text or "").lower())


def save_documents(kb_id: int, documents: list[Document]) -> None:
    settings.bm25_path.mkdir(parents=True, exist_ok=True)
    target = index_file(kb_id)
    temp = target.with_suffix(".pkl.tmp")
    with open(temp, "wb") as handle:
        pickle.dump(documents, handle)
    os.replace(temp, target)
    _cache.pop(kb_id, None)


def load_documents(kb_id: int) -> list[Document]:
    target = index_file(kb_id)
    if not target.exists():
        return []
    with open(target, "rb") as handle:
        return pickle.load(handle)


def _date_ts(value: object, fallback: int) -> int:
    text = normalize_date(value).replace("-", "")
    return int(text) if text.isdigit() else fallback


def _metadata_arrays(documents: list[Document]) -> dict[str, np.ndarray]:
    def column(key: str) -> np.ndarray:
        return np.array([document.metadata.get(key) for document in documents], dtype=object)

    return {
        "lifecycle_status": column("lifecycle_status"),
        "effective_ts": np.array(
            [
                int(item.metadata.get("effective_ts"))
                if item.metadata.get("effective_ts") is not None
                else _date_ts(item.metadata.get("effective_date"), 0)
                for item in documents
            ],
            dtype=np.int64,
        ),
        "repealed_ts": np.array(
            [
                int(item.metadata.get("repealed_ts"))
                if item.metadata.get("repealed_ts") is not None
                else _date_ts(item.metadata.get("repealed_at"), 99991231235959)
                for item in documents
            ],
            dtype=np.int64,
        ),
        "visibility": column("visibility"),
        "department_id": np.array(
            [
                int(item.metadata.get("department_id"))
                if str(item.metadata.get("department_id", "")).lstrip("-").isdigit()
                else -1
                for item in documents
            ],
            dtype=np.int64,
        ),
    }


def _load_index(kb_id: int) -> tuple[list[Document], BM25Okapi, dict[str, np.ndarray]] | None:
    target = index_file(kb_id)
    if not target.exists():
        return None
    mtime = target.stat().st_mtime
    cached = _cache.get(kb_id)
    if cached and cached[0] == mtime:
        return cached[1], cached[2], cached[3]
    documents = load_documents(kb_id)
    if not documents:
        return None
    corpus = [tokenize(document.page_content) for document in documents]
    index = BM25Okapi(corpus)
    arrays = _metadata_arrays(documents)
    _cache[kb_id] = (mtime, documents, index, arrays)
    return documents, index, arrays


def metadata_mask(arrays: dict[str, np.ndarray], scope: RetrievalScope) -> np.ndarray:
    """按检索范围生成 NumPy 布尔掩码，避免逐条判断。"""

    mask = (
        (arrays["lifecycle_status"] == "published")
        & (arrays["effective_ts"] <= scope.today_ts)
        & (arrays["repealed_ts"] > scope.now_ts)
    )
    visibility = arrays["visibility"]
    public_mask = visibility == "public"
    if scope.allow_department:
        if scope.role == "admin":
            department_mask = visibility == "department"
        else:
            department_mask = (visibility == "department") & (
                arrays["department_id"] == int(scope.department_id or -1)
            )
        visibility_mask = department_mask if not scope.allow_public else (public_mask | department_mask)
    else:
        visibility_mask = public_mask if scope.allow_public else np.zeros(len(mask), dtype=bool)
    return mask & visibility_mask


def search(kb_id: int, query: str, k: int, scope: RetrievalScope) -> list[Document]:
    loaded = _load_index(kb_id)
    if loaded is None or k <= 0:
        return []
    documents, index, arrays = loaded
    scores = np.asarray(index.get_scores(tokenize(query)), dtype=np.float64)
    if scores.size == 0:
        return []
    mask = metadata_mask(arrays, scope)
    scores = np.where(mask, scores, -np.inf)
    valid = np.isfinite(scores) & (scores > 0)
    if not valid.any():
        return []
    candidates = np.flatnonzero(valid)
    take = min(k, candidates.size)
    top = candidates[np.argsort(-scores[candidates])[:take]]
    return [documents[int(position)] for position in top]


def index_stats(kb_id: int) -> dict:
    documents = load_documents(kb_id)
    return {
        "chunks": len(documents),
        "published_chunks": sum(
            1 for item in documents if item.metadata.get("lifecycle_status") == "published"
        ),
        "tables": sum(1 for item in documents if item.metadata.get("chunk_type") == "table"),
    }


def invalidate(kb_id: int) -> None:
    _cache.pop(kb_id, None)
