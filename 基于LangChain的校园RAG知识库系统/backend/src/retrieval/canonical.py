import re
import threading
from collections import defaultdict
from dataclasses import dataclass

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from src.retrieval.vector_store import get_vector_store

DUP_THRESHOLD = 0.9
MIN_TEXT_LENGTH = 30
COVERAGE_RATIO = 0.9

_TITLE = re.compile(r"^《[^》]*》")
_ATTACHMENT = re.compile(r"附件\s*(\d+)")
_PUNCT = re.compile(r"[\s，。；：、（）()【】\[\]“”\"'`|*#\-—_]")

_cache: dict[int, dict[str, Document]] = {}
_lock = threading.Lock()


def _normalize(text: str) -> str:
    text = _TITLE.sub("", text or "")
    return _PUNCT.sub("", text)


def _bigrams(text: str) -> set[str]:
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _section_key(text: str, meta: dict) -> str:
    title = str(meta.get("section_title") or "")
    match = _ATTACHMENT.search(title) or _ATTACHMENT.search((text or "")[:160])
    if match:
        return f"附件{match.group(1)}"
    return title[:80]


@dataclass
class _Chunk:
    doc_id: int
    chunk_id: str
    section: str
    normalized: str
    grams: set[str]
    document: Document


def _duplicate_map(kb_id: int) -> dict[str, Document]:
    store = get_vector_store(kb_id)
    data = store.get(include=["documents", "metadatas"])
    grouped: dict[str, list[_Chunk]] = defaultdict(list)
    for store_id, text, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        meta = meta or {}
        doc_id = int(meta.get("document_id") or 0)
        normalized = _normalize(text or "")
        if doc_id <= 0 or len(normalized) < MIN_TEXT_LENGTH:
            continue
        section = _section_key(text or "", meta)
        grouped[section].append(
            _Chunk(
                doc_id=doc_id,
                chunk_id=str(meta.get("chunk_id") or store_id),
                section=section,
                normalized=normalized,
                grams=_bigrams(normalized),
                document=Document(page_content=text or "", metadata=meta),
            )
        )

    mapping: dict[str, Document] = {}
    for items in grouped.values():
        doc_ids = sorted({item.doc_id for item in items})
        if len(doc_ids) < 2:
            continue
        canonical_id = doc_ids[0]
        canonical_items = [item for item in items if item.doc_id == canonical_id]
        for item in items:
            if item.doc_id == canonical_id:
                continue
            best: _Chunk | None = None
            best_score = 0.0
            for candidate in canonical_items:
                union = len(item.grams | candidate.grams)
                if not union:
                    continue
                score = len(item.grams & candidate.grams) / union
                if score > best_score:
                    best, best_score = candidate, score
            if best is None or best_score < DUP_THRESHOLD:
                continue
            if len(best.normalized) < COVERAGE_RATIO * len(item.normalized):
                continue
            mapping[item.chunk_id] = best.document
    return mapping


def _get_map(kb_id: int) -> dict[str, Document]:
    with _lock:
        cached = _cache.get(kb_id)
    if cached is not None:
        return cached
    mapping = _duplicate_map(kb_id)
    with _lock:
        _cache[kb_id] = mapping
    return mapping


def invalidate(kb_id: int) -> None:
    with _lock:
        _cache.pop(kb_id, None)


class CanonicalizeRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseRetriever
    kb_id: int

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        documents = self.inner.invoke(query)
        mapping = _get_map(self.kb_id)
        if not mapping:
            return documents
        results: list[Document] = []
        seen: set[str] = set()
        for document in documents:
            metadata = document.metadata or {}
            chunk_id = str(metadata.get("chunk_id") or "")
            canonical = mapping.get(chunk_id, document)
            canonical_meta = canonical.metadata or {}
            key = str(canonical_meta.get("chunk_id") or chunk_id)
            if key in seen:
                continue
            seen.add(key)
            if canonical is not document:
                merged = dict(canonical_meta)
                merged["dedup_from_document_id"] = metadata.get("document_id")
                canonical = Document(page_content=canonical.page_content, metadata=merged)
            results.append(canonical)
        return results
