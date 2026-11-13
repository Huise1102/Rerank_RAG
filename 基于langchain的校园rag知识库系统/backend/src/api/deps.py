from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decode_access_token
from src.db.models import Conversation, User
from src.db.session import get_db
from src.services.auth_service import resolve_session_user

bearer_scheme = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    user, _ = await resolve_session_user(db, jti)
    request.state.jti = jti
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权限访问")
    return user


async def get_owned_conversation(db: AsyncSession, conversation_id: int, user: User) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return conversation
