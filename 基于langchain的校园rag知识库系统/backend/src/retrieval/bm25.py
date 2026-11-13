import os
import pickle
from pathlib import Path

from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from src.core.config import settings

_cache: dict[int, tuple[float, int, BM25Retriever]] = {}


def index_file(kb_id: int) -> Path:
    return settings.bm25_path / f"kb_{kb_id}.pkl"


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


def get_retriever(kb_id: int, k: int) -> BM25Retriever | None:
    target = index_file(kb_id)
    if not target.exists():
        return None
    mtime = target.stat().st_mtime
    cached = _cache.get(kb_id)
    if cached and cached[0] == mtime and cached[1] == k:
        return cached[2]
    documents = load_documents(kb_id)
    if not documents:
        return None
    retriever = BM25Retriever.from_documents(documents, k=k)
    _cache[kb_id] = (mtime, k, retriever)
    return retriever


def invalidate(kb_id: int) -> None:
    _cache.pop(kb_id, None)
