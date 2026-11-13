from functools import lru_cache

import chromadb
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from src.core.config import settings


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        model_kwargs={"device": settings.embedding_device},
        encode_kwargs={"normalize_embeddings": True, "batch_size": settings.embedding_batch_size},
        cache_folder=str(settings.hf_cache_path / "hub"),
    )


def collection_name(kb_id: int) -> str:
    return f"kb_{kb_id}"


def get_vector_store(kb_id: int) -> Chroma:
    return Chroma(
        collection_name=collection_name(kb_id),
        embedding_function=get_embeddings(),
        persist_directory=str(settings.chroma_path),
    )


def delete_collection(kb_id: int) -> None:
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    try:
        client.delete_collection(collection_name(kb_id))
    except Exception:
        pass
