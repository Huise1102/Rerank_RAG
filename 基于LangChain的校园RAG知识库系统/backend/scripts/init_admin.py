import asyncio
import secrets
import string
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.core.security import hash_password  # noqa: E402
from src.db.models import User  # noqa: E402
from src.db.session import async_session_factory  # noqa: E402


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


async def ensure_admin() -> None:
    async with async_session_factory() as db:
        exists = await db.execute(select(User).where(User.username == settings.admin_username))
        if exists.scalar_one_or_none() is not None:
            print(f"管理员账号已存在，跳过创建：{settings.admin_username}")
            return

        password = settings.admin_initial_password or generate_password()
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(password),
            display_name=settings.admin_display_name,
            role="admin",
            status="active",
            must_change_password=True,
        )
        db.add(admin)
        await db.commit()
        if settings.admin_initial_password:
            print(f"已创建管理员：{settings.admin_username}，初始密码来自环境变量，首次登录需修改")
        else:
            print(f"已创建管理员：{settings.admin_username}，初始密码：{password}")
            print("该密码仅显示一次，请立即保存并在首次登录后修改")


if __name__ == "__main__":
    asyncio.run(ensure_admin())
