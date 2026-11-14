import asyncio
import json
import logging
from datetime import date, datetime, timedelta

from redis.exceptions import RedisError
from sqlalchemy import select

from src.core.config import settings
from src.db.models import Document, Notification
from src.db.session import async_session_factory
from src.services.cache_service import SEMANTIC_PREFIX
from src.services.notification_service import notify_department_admins
from src.utils import cache

logger = logging.getLogger(__name__)

REVIEW_WINDOW_DAYS = 30
REMINDER_COOLDOWN_DAYS = 7
DAILY_SECONDS = 24 * 3600
STARTUP_DELAY_SECONDS = 30
SAMPLE_LIST_KEY = "rag:cache:samples"

_tasks: list[asyncio.Task] = []


async def send_review_reminders() -> dict:
    """扫描即将到期或已过期的文档，给对应部门管理员发送站内提醒。"""
    deadline = date.today() + timedelta(days=REVIEW_WINDOW_DAYS)
    since = datetime.utcnow() - timedelta(days=REMINDER_COOLDOWN_DAYS)
    async with async_session_factory() as db:
        documents = (
            await db.execute(
                select(Document)
                .where(
                    Document.doc_status == "active",
                    Document.review_due_date.is_not(None),
                    Document.review_due_date <= deadline,
                )
                .order_by(Document.review_due_date)
            )
        ).scalars().all()
        created = 0
        for document in documents:
            link = f"/knowledge?doc={document.id}"
            exists = (
                await db.execute(
                    select(Notification.id)
                    .where(
                        Notification.type == "review",
                        Notification.link == link,
                        Notification.created_at >= since,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue
            created += await notify_department_admins(
                db,
                document.department_id,
                "review",
                "文档到期复核提醒",
                f"《{document.title}》复核日期为 {document.review_due_date.isoformat()}",
                link,
            )
        await db.commit()
    return {"documents": len(documents), "notifications": created}


async def cleanup_cache() -> dict:
    """清理失效缓存：语义向量指向的答案已过期，缓存样本对应的答案已过期。"""
    if not await cache.ping():
        return {"skipped": "redis 不可用"}
    client = cache.get_client()
    removed_entries = 0
    try:
        for key in await cache.scan_keys(f"{SEMANTIC_PREFIX}*"):
            rows = await client.lrange(key, 0, -1)
            ttl = await client.ttl(key)
            kept: list[str] = []
            for row in rows:
                try:
                    item = json.loads(row)
                except (TypeError, ValueError):
                    continue
                target = item.get("key")
                if target and await client.exists(target):
                    kept.append(row)
                else:
                    removed_entries += 1
            if len(kept) == len(rows):
                continue
            pipe = client.pipeline()
            pipe.delete(key)
            if kept:
                pipe.rpush(key, *kept)
                if ttl and ttl > 0:
                    pipe.expire(key, ttl)
            await pipe.execute()
    except (RedisError, OSError) as exc:
        logger.warning("语义缓存清理失败：%s", exc)

    removed_samples = 0
    try:
        rows = await client.lrange(SAMPLE_LIST_KEY, 0, -1)
        kept = []
        for row in rows:
            try:
                item = json.loads(row)
            except (TypeError, ValueError):
                continue
            target = item.get("key")
            if item.get("flagged") or (target and await client.exists(target)):
                kept.append(row)
            else:
                removed_samples += 1
        if removed_samples:
            pipe = client.pipeline()
            pipe.delete(SAMPLE_LIST_KEY)
            if kept:
                pipe.rpush(SAMPLE_LIST_KEY, *kept)
            await pipe.execute()
    except (RedisError, OSError) as exc:
        logger.warning("缓存样本清理失败：%s", exc)

    return {"semantic_entries": removed_entries, "samples": removed_samples}


async def _run_daily(name: str, handler) -> None:
    """启动后延迟执行一次，之后按天重复，单次失败不影响后续调度。"""
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            result = await handler()
            logger.info("定时任务 %s 执行完成：%s", name, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("定时任务 %s 执行失败：%s", name, exc)
        await asyncio.sleep(DAILY_SECONDS)


def start_scheduler() -> None:
    if not settings.scheduler_enabled or _tasks:
        return
    _tasks.append(asyncio.create_task(_run_daily("文档复核提醒", send_review_reminders)))
    _tasks.append(asyncio.create_task(_run_daily("缓存清理", cleanup_cache)))
    logger.info("定时任务已启动")


async def stop_scheduler() -> None:
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()
