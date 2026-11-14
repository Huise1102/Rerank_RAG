import asyncio
import hashlib
import json
import math
import time
import unicodedata
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Document,
    DocumentVersion,
    KnowledgeBase,
    RetrievalLog,
    SystemSetting,
    utcnow,
)
from src.db.session import async_session_factory
from src.generation.chain import DISCLAIMER, REFUSAL
from src.retrieval.scope import RetrievalScope
from src.utils import cache

CACHE_SETTINGS_KEY = "cache_settings"
SEMANTIC_PREFIX = "rag:semvec:"
SEMANTIC_MAX_ENTRIES = 50
DEFAULT_CACHE_SETTINGS = {
    "enabled": True,
    "semantic_enabled": False,
    "ttl_days": 7,
    "semantic_threshold": 0.85,
    "updated_at": "",
}


def default_cache_settings() -> dict:
    return dict(DEFAULT_CACHE_SETTINGS)


async def get_cache_settings(db: AsyncSession) -> dict:
    row = await db.get(SystemSetting, CACHE_SETTINGS_KEY)
    settings = default_cache_settings()
    if row and row.value:
        settings.update({key: value for key, value in row.value.items() if value is not None})
    return settings


async def update_cache_settings(db: AsyncSession, values: dict) -> dict:
    if values.get("semantic_enabled") and not await semantic_supported():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Redis 不可用，无法开启语义缓存",
        )
    row = await db.get(SystemSetting, CACHE_SETTINGS_KEY)
    merged = default_cache_settings()
    if row and row.value:
        merged.update(row.value)
    merged.update({key: value for key, value in values.items() if value is not None})
    merged["ttl_days"] = max(1, min(int(merged["ttl_days"]), 30))
    merged["semantic_threshold"] = max(0.75, min(float(merged["semantic_threshold"]), 1.0))
    merged["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if row is None:
        db.add(SystemSetting(key=CACHE_SETTINGS_KEY, value=merged))
    else:
        row.value = merged
    await db.commit()
    return merged


async def semantic_supported() -> bool:
    """语义缓存用本地嵌入模型计算相似度，只要求 Redis 可用。"""
    return await cache.ping()


def normalize_question(question: str) -> str:
    """统一全半角、空白与英文大小写，不改变问题语义。"""
    text = unicodedata.normalize("NFKC", question or "")
    text = " ".join(text.split())
    return text.strip().lower()


def scope_fingerprint(scope: RetrievalScope) -> str:
    raw = (
        f"{scope.role}|{scope.department_id or 0}|"
        f"{int(scope.allow_public)}|{int(scope.allow_department)}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_cache_key(
    kb_id: int,
    strategy_id: int | None,
    scope: RetrievalScope,
    index_epoch: int,
    question: str,
) -> str:
    question_hash = hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()[:32]
    return (
        f"rag:cache:{kb_id}:{strategy_id or 0}:{scope_fingerprint(scope)}:"
        f"{int(index_epoch or 1)}:{question_hash}"
    )


def build_semantic_key(
    kb_id: int, strategy_id: int | None, scope: RetrievalScope, index_epoch: int
) -> str:
    return (
        f"{SEMANTIC_PREFIX}{kb_id}:{strategy_id or 0}:"
        f"{scope_fingerprint(scope)}:{int(index_epoch or 1)}"
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


async def _embed_question(question: str) -> list[float] | None:
    """用检索同一套嵌入模型编码问题，模型不可用时语义缓存降级。"""

    def encode() -> list[float]:
        from src.retrieval.vector_store import get_embeddings

        return get_embeddings().embed_query(normalize_question(question))

    try:
        vector = await asyncio.to_thread(encode)
    except Exception:
        return None
    return [float(item) for item in vector]


async def _store_semantic_vector(
    kb_id: int,
    index_epoch: int,
    strategy_id: int | None,
    scope: RetrievalScope,
    question: str,
    cache_key: str,
    ttl_seconds: int,
) -> None:
    vector = await _embed_question(question)
    if vector is None:
        return
    row = json.dumps(
        {
            "key": cache_key,
            "question": normalize_question(question),
            "vector": vector,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        ensure_ascii=False,
    )
    semantic_key = build_semantic_key(kb_id, strategy_id, scope, index_epoch)
    try:
        client = cache.get_client()
        pipe = client.pipeline()
        pipe.lpush(semantic_key, row)
        pipe.ltrim(semantic_key, 0, SEMANTIC_MAX_ENTRIES - 1)
        pipe.expire(semantic_key, max(ttl_seconds, 60))
        await pipe.execute()
    except Exception:
        return


async def _semantic_lookup(
    kb_id: int,
    index_epoch: int,
    strategy_id: int | None,
    scope: RetrievalScope,
    question: str,
    threshold: float,
) -> tuple[dict | None, str | None]:
    """在同一知识库、策略、权限与索引版本内做向量相似度匹配。"""
    vector = await _embed_question(question)
    if vector is None:
        return None, None
    semantic_key = build_semantic_key(kb_id, strategy_id, scope, index_epoch)
    try:
        rows = await cache.get_client().lrange(semantic_key, 0, SEMANTIC_MAX_ENTRIES - 1)
    except Exception:
        return None, None

    best_row: dict | None = None
    best_score = 0.0
    for row in rows:
        try:
            item = json.loads(row)
        except (TypeError, ValueError):
            continue
        score = cosine_similarity(vector, item.get("vector") or [])
        if score > best_score:
            best_score = score
            best_row = item
    if best_row is None or best_score < threshold:
        return None, None
    matched_key = best_row.get("key")
    entry = await cache.get_json(matched_key) if matched_key else None
    if entry is None:
        return None, None
    result = dict(entry)
    result["match_type"] = "semantic"
    result["similarity"] = round(best_score, 4)
    result["matched_question"] = best_row.get("question", "")
    return result, matched_key


def _scope_allows_document(scope: RetrievalScope, document: Document) -> bool:
    if document.visibility == "public":
        return scope.allow_public
    if document.visibility == "admin_only":
        return False
    if not scope.allow_department:
        return False
    if scope.role == "admin":
        return True
    return document.department_id is not None and document.department_id == scope.department_id


async def _citation_valid(
    db: AsyncSession, citation: dict, kb_id: int, scope: RetrievalScope
) -> bool:
    document_id = citation.get("document_id")
    version_id = citation.get("version_id")
    if not document_id or not version_id:
        return False
    document = await db.get(Document, int(document_id))
    if document is None or document.kb_id != kb_id:
        return False
    if document.doc_status != "active":
        return False
    if document.current_version_id != int(version_id):
        return False
    if not _scope_allows_document(scope, document):
        return False
    version = await db.get(DocumentVersion, int(version_id))
    if version is None or version.document_id != document.id:
        return False
    if version.lifecycle_status != "published" or version.repealed_at is not None:
        return False
    if version.effective_date is not None and version.effective_date > scope.now.date():
        return False
    return True


async def citations_valid(
    db: AsyncSession, citations: list[dict], kb_id: int, scope: RetrievalScope
) -> bool:
    if not citations:
        return False
    for citation in citations:
        if not await _citation_valid(db, citation, kb_id, scope):
            return False
    return True


async def lookup_cached_answer(
    db: AsyncSession,
    kb: KnowledgeBase,
    strategy_id: int | None,
    scope: RetrievalScope,
    question: str,
) -> dict | None:
    settings = await get_cache_settings(db)
    if not settings.get("enabled"):
        return None
    key = build_cache_key(kb.id, strategy_id, scope, kb.index_epoch, question)
    entry = await cache.get_json(key)
    entry_key = key
    if entry is None:
        if not settings.get("semantic_enabled"):
            return None
        entry, entry_key = await _semantic_lookup(
            kb.id,
            kb.index_epoch,
            strategy_id,
            scope,
            question,
            float(settings.get("semantic_threshold", 0.95)),
        )
        if entry is None or entry_key is None:
            return None
    else:
        entry = dict(entry)
        entry["match_type"] = "exact"
    if int(entry.get("index_epoch", 0)) != int(kb.index_epoch or 1):
        await cache.delete(entry_key)
        return None
    if not await citations_valid(db, entry.get("citations") or [], kb.id, scope):
        await cache.delete(entry_key)
        return None
    return entry


async def store_cached_answer(
    kb_id: int,
    index_epoch: int,
    strategy_id: int | None,
    strategy_name: str,
    scope: RetrievalScope,
    question: str,
    answer: str,
    citations: list[dict],
    detail: dict,
) -> bool:
    settings_row = None
    async with async_session_factory() as db:
        settings_row = await get_cache_settings(db)
        if not settings_row.get("enabled"):
            return False
        if not citations or not await citations_valid(db, citations, kb_id, scope):
            return False
    text = (answer or "").strip()
    if not text or text == REFUSAL:
        return False
    if detail.get("transfer_required"):
        return False
    if scope.role not in {"admin", "dept_admin", "staff", "student"}:
        return False
    ttl_seconds = int(settings_row["ttl_days"]) * 86400
    now = utcnow()
    entry = {
        "answer": text,
        "citations": citations,
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "kb_id": kb_id,
        "index_epoch": int(index_epoch or 1),
        "document_versions": detail.get("versions", []),
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds"),
        "disclaimer": DISCLAIMER,
        "detail": detail,
    }
    key = build_cache_key(kb_id, strategy_id, scope, index_epoch, question)
    stored = await cache.set_json(key, entry, ttl_seconds)
    if stored:
        if settings_row.get("semantic_enabled"):
            await _store_semantic_vector(
                kb_id,
                index_epoch,
                strategy_id,
                scope,
                question,
                key,
                ttl_seconds,
            )
        await cache.push_sample(
            key,
            {
                "kb_id": kb_id,
                "question": normalize_question(question),
                "created_at": entry["created_at"],
                "index_epoch": entry["index_epoch"],
            },
        )
    return stored


async def _clear_semantic_entries(kb_id: int | None) -> None:
    pattern = f"{SEMANTIC_PREFIX}{kb_id}:*" if kb_id else f"{SEMANTIC_PREFIX}*"
    for key in await cache.scan_keys(pattern):
        await cache.delete(key)


async def clear_cache(kb_id: int | None = None, document_id: int | None = None) -> int:
    if document_id is not None:
        pattern = f"rag:cache:{kb_id}:*" if kb_id else "rag:cache:*"
        removed = 0
        for key in await cache.scan_keys(pattern):
            if key == "rag:cache:samples":
                continue
            entry = await cache.get_json(key)
            if entry is None:
                continue
            citations = entry.get("citations") or []
            if any(str(item.get("document_id")) == str(document_id) for item in citations):
                await cache.delete(key)
                removed += 1
        await _clear_semantic_entries(kb_id)
        return removed
    pattern = f"rag:cache:{kb_id}:*" if kb_id else "rag:cache:*"
    keys = await cache.scan_keys(pattern)
    for key in keys:
        await cache.delete(key)
    await _clear_semantic_entries(kb_id)
    return len(keys)


async def cache_stats(db: AsyncSession, days: int = 7) -> dict:
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    totals = (
        await db.execute(
            select(
                func.count(RetrievalLog.id),
                func.sum(case((RetrievalLog.cache_hit.is_(True), 1), else_=0)),
            ).where(RetrievalLog.created_at >= since)
        )
    ).one()
    total = int(totals[0] or 0)
    hits = int(totals[1] or 0)
    hit_avg = (
        await db.execute(
            select(func.avg(RetrievalLog.duration_ms)).where(
                RetrievalLog.created_at >= since, RetrievalLog.cache_hit.is_(True)
            )
        )
    ).scalar()
    miss_avg = (
        await db.execute(
            select(func.avg(RetrievalLog.duration_ms)).where(
                RetrievalLog.created_at >= since, RetrievalLog.cache_hit.is_(False)
            )
        )
    ).scalar()
    by_kb_rows = (
        await db.execute(
            select(
                RetrievalLog.kb_id,
                KnowledgeBase.name,
                func.count(RetrievalLog.id),
                func.sum(case((RetrievalLog.cache_hit.is_(True), 1), else_=0)),
            )
            .outerjoin(KnowledgeBase, KnowledgeBase.id == RetrievalLog.kb_id)
            .where(RetrievalLog.created_at >= since)
            .group_by(RetrievalLog.kb_id, KnowledgeBase.name)
        )
    ).all()
    by_kb = [
        {
            "kb_id": row[0],
            "kb_name": row[1] or "未关联",
            "questions": int(row[2] or 0),
            "hits": int(row[3] or 0),
        }
        for row in by_kb_rows
    ]
    return {
        "days": days,
        "questions": total,
        "hits": hits,
        "hit_rate": round(hits / total, 4) if total else 0,
        "avg_hit_ms": int(hit_avg or 0),
        "avg_miss_ms": int(miss_avg or 0),
        "redis_available": await cache.ping(),
        "by_kb": by_kb,
    }
