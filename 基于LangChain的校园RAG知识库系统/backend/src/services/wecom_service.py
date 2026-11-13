import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import AuthIdentity, User
from src.utils.cache import get_client

logger = logging.getLogger(__name__)

PROVIDER = "wecom"
STATE_PREFIX = "wecom:state:"
TOKEN_KEY = "wecom:access_token"
STATE_TTL_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 10


def is_enabled() -> bool:
    """企业微信登录只在开关打开且四项配置齐全时生效。"""
    return bool(settings.wecom_enabled and settings.wecom_configured)


def ensure_enabled() -> None:
    if not is_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="企业微信登录未启用"
        )


def _redirect_uri() -> str:
    return settings.wecom_redirect_uri


def build_authorize_url(state: str) -> str:
    """拼接企业微信网页授权链接，静默授权只获取成员身份。"""
    params = {
        "appid": settings.wecom_corp_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "snsapi_base",
        "state": state,
        "agentid": settings.wecom_agent_id,
    }
    return (
        "https://open.weixin.qq.com/connect/oauth2/authorize?"
        f"{urlencode(params)}#wechat_redirect"
    )


async def create_state(redirect: str | None = None) -> str:
    """生成一次性 state 并暂存回跳地址，用于回调校验。"""
    state = secrets.token_urlsafe(24)
    try:
        await get_client().set(
            f"{STATE_PREFIX}{state}", redirect or "", ex=STATE_TTL_SECONDS
        )
    except (RedisError, OSError) as exc:
        logger.warning("企业微信 state 写入失败：%s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="登录状态存储不可用，请稍后重试"
        ) from exc
    return state


async def consume_state(state: str) -> str:
    """校验并立即失效 state，返回登录成功后前端可回跳的路径。"""
    if not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="缺少登录状态参数")
    key = f"{STATE_PREFIX}{state}"
    client = get_client()
    try:
        redirect = await client.get(key)
        await client.delete(key)
    except (RedisError, OSError) as exc:
        logger.warning("企业微信 state 读取失败：%s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="登录状态存储不可用，请稍后重试"
        ) from exc
    if redirect is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="登录状态已失效，请重新发起企业微信登录"
        )
    return redirect


async def _call(path: str, params: dict[str, str]) -> dict:
    url = f"{settings.wecom_api_base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("企业微信接口请求失败：%s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="企业微信服务暂不可用，请稍后重试"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="企业微信返回数据格式异常"
        ) from exc
    if int(data.get("errcode", 0)) != 0:
        logger.warning(
            "企业微信接口返回错误：path=%s errcode=%s errmsg=%s",
            path,
            data.get("errcode"),
            data.get("errmsg"),
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"企业微信接口返回错误：{data.get('errmsg') or data.get('errcode')}",
        )
    return data


async def get_access_token() -> str:
    """获取企业微信 access_token，按官方有效期前提前缓存复用。"""
    client = get_client()
    try:
        cached = await client.get(TOKEN_KEY)
    except (RedisError, OSError):
        cached = None
    if cached:
        return cached

    data = await _call(
        "/cgi-bin/gettoken",
        {"corpid": settings.wecom_corp_id, "corpsecret": settings.wecom_secret},
    )
    token = str(data.get("access_token") or "")
    if not token:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="企业微信未返回访问凭证"
        )
    expires_in = max(int(data.get("expires_in", 7200)) - 300, 60)
    try:
        await client.set(TOKEN_KEY, token, ex=expires_in)
    except (RedisError, OSError):
        pass
    return token


async def get_userid(code: str) -> str:
    """用网页授权 code 换取企业微信成员账号。"""
    token = await get_access_token()
    data = await _call(
        "/cgi-bin/auth/getuserinfo", {"access_token": token, "code": code}
    )
    userid = str(data.get("userid") or data.get("UserId") or "")
    if not userid:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="该企业微信账号不属于本企业成员，无法登录",
        )
    return userid


async def resolve_user(db: AsyncSession, external_id: str) -> User:
    """按企业微信成员账号查找站内用户，首次登录按同名账号自动绑定。"""
    result = await db.execute(
        select(AuthIdentity).where(
            AuthIdentity.provider == PROVIDER,
            AuthIdentity.external_id == external_id,
        )
    )
    identity = result.scalar_one_or_none()
    if identity is not None:
        user = await db.get(User, identity.user_id)
        if user is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="绑定的系统账号不存在，请联系管理员"
            )
    else:
        result = await db.execute(select(User).where(User.username == external_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="企业微信账号未绑定系统账号，请联系管理员开通",
            )
        db.add(
            AuthIdentity(provider=PROVIDER, external_id=external_id, user_id=user.id)
        )
        await db.flush()

    if user.status != "active":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="账号已被禁用，请联系管理员"
        )
    return user
