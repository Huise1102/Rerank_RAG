from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip
from src.db.session import get_db
from src.schemas.user import (
    UserOut,
    WecomCallbackRequest,
    WecomLoginResponse,
)
from src.services import wecom_service
from src.services.auth_service import issue_session

router = APIRouter(prefix="/auth/wecom", tags=["企业微信登录"])


@router.get("/status")
async def wecom_status():
    """前端登录页据此决定是否展示企业微信登录按钮。"""
    return {"enabled": wecom_service.is_enabled(), "provider": wecom_service.PROVIDER}


@router.get("/authorize")
async def wecom_authorize(redirect: str | None = None):
    """生成企业微信网页授权地址，state 与回跳路径一次性暂存。"""
    wecom_service.ensure_enabled()
    target = redirect if redirect and redirect.startswith("/") and not redirect.startswith("//") else None
    state = await wecom_service.create_state(target)
    return {"authorize_url": wecom_service.build_authorize_url(state), "state": state}


@router.post("/callback", response_model=WecomLoginResponse)
async def wecom_callback(
    payload: WecomCallbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """用网页授权 code 换取企业微信身份并签发站内登录会话。"""
    wecom_service.ensure_enabled()
    redirect = await wecom_service.consume_state(payload.state)
    userid = await wecom_service.get_userid(payload.code)
    user = await wecom_service.resolve_user(db, userid)
    token, user = await issue_session(
        db,
        user,
        client_ip(request),
        request.headers.get("user-agent"),
        action="login_wecom",
        detail=f"企业微信成员 {userid}",
    )
    return WecomLoginResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        redirect=redirect or None,
    )
