from fastapi import APIRouter

from src.api.routes import (
    admin_feedback,
    admin_settings,
    admin_users,
    auth,
    chat,
    feedback,
    knowledge,
    logs,
    strategies,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(knowledge.router)
api_router.include_router(strategies.router)
api_router.include_router(feedback.router)
api_router.include_router(admin_users.router)
api_router.include_router(admin_feedback.router)
api_router.include_router(admin_settings.router)
api_router.include_router(logs.router)
