from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    department: str | None = None
    status: str
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=72)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern="^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=72)
    role: str = Field(pattern="^(admin|user)$")
    department: str | None = Field(default=None, max_length=100)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    department: str | None = Field(default=None, max_length=100)


class ResetPasswordRequest(BaseModel):
    new_password: str | None = Field(default=None, min_length=8, max_length=72)


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    page_size: int
