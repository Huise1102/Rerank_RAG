from fastapi import APIRouter

from src.api.routes import (
    admin_agent,
    admin_cache,
    admin_dashboard,
    admin_evaluation,
    admin_feedback,
    admin_gaps,
    admin_review,
    admin_settings,
    admin_users,
    auth,
    auth_wecom,
    chat,
    departments,
    documents,
    feedback,
    knowledge,
    logs,
    notifications,
    strategies,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(auth_wecom.router)
api_router.include_router(chat.router)
api_router.include_router(knowledge.router)
api_router.include_router(departments.router)
api_router.include_router(documents.router)
api_router.include_router(admin_evaluation.router)
api_router.include_router(admin_cache.router)
api_router.include_router(admin_agent.router)
api_router.include_router(admin_dashboard.router)
api_router.include_router(admin_gaps.router)
api_router.include_router(admin_review.router)
api_router.include_router(strategies.router)
api_router.include_router(feedback.router)
api_router.include_router(notifications.router)
api_router.include_router(admin_users.router)
api_router.include_router(admin_feedback.router)
api_router.include_router(admin_settings.router)
api_router.include_router(logs.router)
