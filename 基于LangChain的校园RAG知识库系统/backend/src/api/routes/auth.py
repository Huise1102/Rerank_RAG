from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, get_current_user
from src.db.models import User
from src.db.session import get_db
from src.schemas.user import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserOut,
)
from src.services.auth_service import authenticate, change_password, revoke_session

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token, user = await authenticate(
        db,
        payload.username.strip(),
        payload.password,
        client_ip(request),
        request.headers.get("user-agent"),
    )
    return LoginResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/logout")
async def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await revoke_session(db, request.state.jti)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.post("/change-password")
async def change_own_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await change_password(db, user, payload.old_password, payload.new_password, request.state.jti)
    return {"ok": True}
