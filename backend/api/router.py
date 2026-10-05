# backend/api/router.py
# 总路由聚合：认证 / 对话 / 我的。

from fastapi import APIRouter

from backend.api.v1.asset import router as asset_router
from backend.api.v1.auth import router as auth_router
from backend.api.v1.channels import router as channels_router
from backend.api.v1.chat import router as chat_router
from backend.api.v1.me import router as me_router
from backend.api.v1.reports import router as reports_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(chat_router)
api_router.include_router(me_router)
api_router.include_router(asset_router)
api_router.include_router(reports_router)
api_router.include_router(channels_router)


@api_router.get("/ping", tags=["系统"])
async def ping():
    return {"pong": True, "module": "office-agent"}
