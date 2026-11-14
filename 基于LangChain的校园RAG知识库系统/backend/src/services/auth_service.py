import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.security import create_access_token, hash_password, verify_password
from src.db.models import AuditLog, AuthSession, User, utcnow
from src.utils.ratelimit import get_counter, increment_counter, reset_counter


def _login_key(ip: str, username: str) -> str:
    return f"login:fail:{ip}:{username.lower()}"


async def authenticate(
    db: AsyncSession,
    username: str,
    password: str,
    ip: str,
    user_agent: str | None,
) -> tuple[str, User]:
    key = _login_key(ip, username)
    failures = await get_counter(key, settings.login_window_seconds)
    if failures >= settings.login_max_failures:
        wait_minutes = max(1, settings.login_window_seconds // 60)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"登录失败次数过多，请在 {wait_minutes} 分钟后重试",
        )

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        await increment_counter(key, settings.login_window_seconds)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="账号不存在，请检查用户名")
    if not verify_password(password, user.password_hash):
        failures = await increment_counter(key, settings.login_window_seconds)
        remaining = max(settings.login_max_failures - failures, 0)
        detail = f"密码错误，还可尝试 {remaining} 次" if remaining else "密码错误，本次登录已锁定"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail)
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="账号已被禁用，请联系管理员")

    await reset_counter(key)
    return await issue_session(db, user, ip, user_agent, action="login")


async def issue_session(
    db: AsyncSession,
    user: User,
    ip: str,
    user_agent: str | None,
    action: str = "login",
    detail: str | None = None,
) -> tuple[str, User]:
    """为用户签发登录会话并写入审计日志，账号密码与企业微信登录共用。"""
    jti = uuid.uuid4().hex
    token, expires_at = create_access_token(user.id, jti)
    db.add(
        AuthSession(
            user_id=user.id,
            jti=jti,
            expires_at=expires_at,
            ip=ip,
            user_agent=(user_agent or "")[:255],
        )
    )
    user.last_login_at = utcnow()
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action=action,
            target_type="user",
            target_id=str(user.id),
            detail=detail,
            ip=ip,
        )
    )
    await db.commit()
    await db.refresh(user)
    return token, user


async def revoke_session(db: AsyncSession, jti: str) -> None:
    result = await db.execute(select(AuthSession).where(AuthSession.jti == jti))
    session = result.scalar_one_or_none()
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        await db.commit()


async def revoke_all_sessions(db: AsyncSession, user_id: int, keep_jti: str | None = None) -> None:
    result = await db.execute(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    )
    for session in result.scalars().all():
        if keep_jti and session.jti == keep_jti:
            continue
        session.revoked_at = utcnow()
    await db.commit()


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="密码至少 8 位")
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="密码过长")
    if not any(ch.isdigit() for ch in password) or not any(ch.isalpha() for ch in password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="密码需同时包含字母和数字")


async def change_password(
    db: AsyncSession, user: User, old_password: str, new_password: str, current_jti: str
) -> None:
    if not verify_password(old_password, user.password_hash):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="原密码不正确")
    validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.updated_at = utcnow()
    await db.commit()
    await revoke_all_sessions(db, user.id, keep_jti=current_jti)


async def resolve_session_user(db: AsyncSession, jti: str) -> tuple[User, AuthSession]:
    result = await db.execute(select(AuthSession).where(AuthSession.jti == jti))
    session = result.scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    if session.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="登录状态已过期")
    user = await db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="账号已被禁用")
    return user, session
