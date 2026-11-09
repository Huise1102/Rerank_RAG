"""会话与消息持久化。

本模块只保留会话管理、消息落库与事件流编码，供问答服务复用。
消息只存四个 JSON 列，策略与统计信息随延迟列一起存，读取时再拆出。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.conversation import Conversation
from src.db.models.message import Message

logger = get_logger(__name__)

# 会话隔离标识：不同项目共用同一数据库，但只能读写自己的会话
APP_KEY = "agent"


async def create_conversation(
    db: AsyncSession, kb_id: int, *, title: str | None = None, app_key: str = APP_KEY
) -> Conversation:
    conv = Conversation(kb_id=kb_id, title=(title or None), app_key=app_key)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_conversation(
    db: AsyncSession, conversation_id: int, app_key: str = APP_KEY
) -> Conversation | None:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.app_key == app_key
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_conversations(
    db: AsyncSession, kb_id: int | None = None, app_key: str = APP_KEY
) -> list[Conversation]:
    stmt = select(Conversation).where(Conversation.app_key == app_key)
    if kb_id is not None:
        stmt = stmt.where(Conversation.kb_id == kb_id)
    rows = await db.execute(stmt.order_by(Conversation.id.desc()))
    return list(rows.scalars().all())


async def delete_conversation(
    db: AsyncSession, conversation_id: int, app_key: str = APP_KEY
) -> bool:
    conv = await get_conversation(db, conversation_id, app_key)
    if conv is None:
        return False
    await db.delete(conv)
    await db.commit()
    return True


async def delete_conversations(
    db: AsyncSession, kb_id: int | None = None, app_key: str = APP_KEY
) -> int:
    """清空本项目的会话，可按知识库过滤，返回删除条数。"""
    stmt = delete(Conversation).where(Conversation.app_key == app_key)
    if kb_id is not None:
        stmt = stmt.where(Conversation.kb_id == kb_id)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount or 0


async def list_messages(db: AsyncSession, conversation_id: int) -> list[dict[str, Any]]:
    """读取会话消息，并把 latency 里嵌着的 strategy / stats 拆出来。"""
    rows = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc())
    )
    out: list[dict[str, Any]] = []
    for m in rows.scalars().all():
        lat = m.latency or {}
        out.append(
            {
                "id": m.id,
                "conversation_id": m.conversation_id,
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "retrieved_docs": m.retrieved_docs,
                "latency": lat,
                "strategy": lat.get("strategy"),
                "stats": lat.get("stats"),
                "created_at": m.created_at,
            }
        )
    return out


async def _recent_history(db: AsyncSession, conversation_id: int) -> list[tuple[str, str]]:
    """取会话历史为角色与内容对，供多轮上下文组装。"""
    rows = await db.execute(
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
    )
    return [(role, content) for role, content in rows.all()]


async def _save_message(
    db: AsyncSession,
    conversation_id: int,
    *,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    retrieved_docs: list[dict[str, Any]] | None = None,
    latency: dict[str, Any] | None = None,
    touch_title: str | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        citations=citations,
        retrieved_docs=retrieved_docs,
        latency=latency,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # 首条用户消息作为会话标题
    if touch_title:
        conv = await db.get(Conversation, conversation_id)
        if conv is not None and not conv.title:
            conv.title = touch_title[:60]
            await db.commit()
    return msg


def sse(payload: dict[str, Any]) -> str:
    """把事件编码为 SSE 一行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
