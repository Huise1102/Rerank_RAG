import time

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import SystemSetting, User

AGENT_SETTINGS_KEY = "agent_settings"
DEFAULT_AGENT_SETTINGS = {
    "enabled": False,
    "kb_ids": [],
    "roles": ["admin", "dept_admin"],
    "max_rounds": 3,
    "max_retrievals": 3,
    "timeout_seconds": 90,
    "updated_at": "",
}


def default_agent_settings() -> dict:
    return dict(DEFAULT_AGENT_SETTINGS)


async def get_agent_settings(db: AsyncSession) -> dict:
    row = await db.get(SystemSetting, AGENT_SETTINGS_KEY)
    settings = default_agent_settings()
    if row and row.value:
        settings.update({key: value for key, value in row.value.items() if value is not None})
    return settings


async def update_agent_settings(db: AsyncSession, values: dict) -> dict:
    row = await db.get(SystemSetting, AGENT_SETTINGS_KEY)
    merged = default_agent_settings()
    if row and row.value:
        merged.update(row.value)
    merged.update({key: value for key, value in values.items() if value is not None})
    merged["max_rounds"] = max(1, min(int(merged["max_rounds"]), 3))
    merged["max_retrievals"] = max(1, min(int(merged["max_retrievals"]), 3))
    merged["timeout_seconds"] = max(10, min(int(merged["timeout_seconds"]), 180))
    merged["roles"] = [item for item in merged["roles"] if item]
    merged["kb_ids"] = [int(item) for item in merged["kb_ids"]]
    merged["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if row is None:
        db.add(SystemSetting(key=AGENT_SETTINGS_KEY, value=merged))
    else:
        row.value = merged
    await db.commit()
    return merged


def deep_allowed(settings: dict, user: User, kb_id: int) -> bool:
    if not settings.get("enabled"):
        return False
    if user.role not in settings.get("roles", []):
        return False
    kb_ids = settings.get("kb_ids") or []
    return not kb_ids or int(kb_id) in {int(item) for item in kb_ids}
