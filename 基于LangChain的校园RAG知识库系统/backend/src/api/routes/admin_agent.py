from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import require_admin
from src.db.models import AuditLog, KnowledgeBase, User
from src.db.session import get_db
from src.services.agent_settings import get_agent_settings, update_agent_settings

router = APIRouter(prefix="/admin/agent", tags=["深度问答"])
VALID_ROLES = {"admin", "dept_admin", "staff", "student"}


class AgentSettingsRequest(BaseModel):
    enabled: bool | None = None
    kb_ids: list[int] | None = None
    roles: list[str] | None = None
    max_rounds: int | None = Field(default=None, ge=1, le=3)
    max_retrievals: int | None = Field(default=None, ge=1, le=3)
    timeout_seconds: int | None = Field(default=None, ge=10, le=180)


@router.get("/settings")
async def read_agent_settings(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    settings = await get_agent_settings(db)
    result = await db.execute(select(KnowledgeBase.id, KnowledgeBase.name).order_by(KnowledgeBase.id))
    settings["knowledge_bases"] = [{"id": row[0], "name": row[1]} for row in result.all()]
    return settings


@router.patch("/settings")
async def write_agent_settings(
    payload: AgentSettingsRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    values = payload.model_dump(exclude_none=True)
    if "roles" in values:
        invalid = [item for item in values["roles"] if item not in VALID_ROLES]
        if invalid:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="角色取值不合法")
    if "kb_ids" in values and values["kb_ids"]:
        existing = (
            (await db.execute(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(values["kb_ids"]))))
            .scalars()
            .all()
        )
        missing = set(values["kb_ids"]) - set(existing)
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"知识库不存在：{sorted(missing)}",
            )
    settings = await update_agent_settings(db, values)
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="update_agent_settings",
            target_type="system_setting",
            target_id="agent_settings",
            detail=(
                f"深度问答开关 {settings['enabled']}，"
                f"知识库 {settings['kb_ids'] or '全部'}，角色 {settings['roles']}"
            ),
        )
    )
    await db.commit()
    return settings
