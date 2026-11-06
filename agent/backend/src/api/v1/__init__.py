"""v1 路由聚合。"""

from fastapi import APIRouter

from src.api.v1 import agent, chat, document, kb, log, model, retrieval

api_router = APIRouter()
api_router.include_router(kb.router)
api_router.include_router(document.router)
api_router.include_router(retrieval.router)
api_router.include_router(chat.router)
api_router.include_router(agent.router)
api_router.include_router(log.router)
api_router.include_router(model.router)

__all__ = ["api_router"]
