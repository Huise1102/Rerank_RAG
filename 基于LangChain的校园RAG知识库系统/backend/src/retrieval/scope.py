from dataclasses import dataclass
from datetime import datetime, timezone

from src.db.models import User

DEPARTMENT_ROLES = {"staff", "dept_admin", "admin"}
ALLOWED_VISIBILITIES = {"public", "department"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class RetrievalScope:
    """描述一次问答请求可访问的文档范围。"""

    user_id: int | None
    role: str
    department_id: int | None
    now: datetime
    allow_public: bool = True
    allow_department: bool = False

    @classmethod
    def from_user(cls, user: User) -> "RetrievalScope":
        allow_department = user.role in DEPARTMENT_ROLES and user.department_id is not None
        return cls(
            user_id=user.id,
            role=user.role,
            department_id=user.department_id,
            now=_utcnow(),
            allow_public=True,
            allow_department=allow_department,
        )

    @property
    def today(self) -> str:
        return self.now.date().isoformat()

    @property
    def now_iso(self) -> str:
        return self.now.isoformat(timespec="seconds")

    @property
    def today_ts(self) -> int:
        return int(self.now.strftime("%Y%m%d"))

    @property
    def now_ts(self) -> int:
        return int(self.now.strftime("%Y%m%d%H%M%S"))

    @property
    def description(self) -> str:
        if self.role == "admin":
            return "public+department(全部部门)"
        if self.allow_department:
            return f"public+department({self.department_id})"
        return "public"


def normalize_date(value: object) -> str:
    text = str(value or "").strip()
    return text[:10]


def build_chroma_filter(scope: RetrievalScope, kb_id: int) -> dict:
    """生成向量召回的 metadata 过滤条件。"""

    conditions: list[dict] = [
        {"kb_id": kb_id},
        {"lifecycle_status": {"$eq": "published"}},
        {"effective_ts": {"$lte": scope.today_ts}},
        {"repealed_ts": {"$gt": scope.now_ts}},
    ]
    if scope.role == "admin":
        conditions.append(
            {"$or": [{"visibility": {"$eq": "public"}}, {"visibility": {"$eq": "department"}}]}
        )
    elif scope.allow_department and scope.department_id is not None:
        conditions.append(
            {
                "$or": [
                    {"visibility": {"$eq": "public"}},
                    {
                        "$and": [
                            {"visibility": {"$eq": "department"}},
                            {"department_id": {"$eq": scope.department_id}},
                        ]
                    },
                ]
            }
        )
    else:
        conditions.append({"visibility": {"$eq": "public"}})
    return {"$and": conditions}


def metadata_allowed(metadata: dict, scope: RetrievalScope) -> bool:
    """按与向量过滤一致的口径判断单个块是否可召回。"""

    if str(metadata.get("lifecycle_status", "")) != "published":
        return False
    effective_ts = metadata.get("effective_ts")
    if effective_ts is not None:
        if int(effective_ts) > scope.today_ts:
            return False
    elif normalize_date(metadata.get("effective_date")) > scope.today:
        return False
    repealed_ts = metadata.get("repealed_ts")
    if repealed_ts is not None:
        if int(repealed_ts) <= scope.now_ts:
            return False
    else:
        repealed_at = str(metadata.get("repealed_at", "") or "")
        if not repealed_at or repealed_at <= scope.now_iso:
            return False

    visibility = str(metadata.get("visibility", ""))
    if visibility not in ALLOWED_VISIBILITIES:
        return False
    if visibility == "public":
        return scope.allow_public
    if not scope.allow_department:
        return False
    if scope.role == "admin":
        return True
    try:
        department_id = int(metadata.get("department_id"))
    except (TypeError, ValueError):
        return False
    return scope.department_id is not None and department_id == scope.department_id
