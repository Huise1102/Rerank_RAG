from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    department_id: int | None = None
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


class WecomCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=1, max_length=128)


class WecomLoginResponse(LoginResponse):
    redirect: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


ROLE_PATTERN = "^(admin|dept_admin|staff|student)$"


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern="^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=72)
    role: str = Field(pattern=ROLE_PATTERN)
    department_id: int | None = None
    department: str | None = Field(default=None, max_length=100)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, pattern=ROLE_PATTERN)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    department_id: int | None = None
    department: str | None = Field(default=None, max_length=100)


class ResetPasswordRequest(BaseModel):
    new_password: str | None = Field(default=None, min_length=8, max_length=72)


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    page_size: int
