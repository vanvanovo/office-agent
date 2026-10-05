# backend/api/v1/me.py
# 「我的」接口：当前身份 / 我的预订 / 站内信（V3 启用提醒推送，先提供读取）。

from fastapi import APIRouter, Depends

from backend.config import get_settings
from backend.core.a2a_client import call_agent
from backend.db.engine import execute, fetch_all
from backend.dependencies import get_current_user

router = APIRouter()
settings = get_settings()


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@router.get("/me/bookings")
async def my_bookings(user: dict = Depends(get_current_user)):
    r = await call_agent(settings.meeting_query_agent_url, "my_bookings", "我的预订",
                         employee={"id": user["user_id"], "role": user["role"]})
    bookings = ((r.get("result") or {}).get("data") or {}).get("bookings", []) \
        if r.get("state") == "completed" else []
    return {"bookings": bookings}


@router.get("/me/messages")
async def my_messages(user: dict = Depends(get_current_user)):
    rows = await fetch_all(
        "app",
        "SELECT msg_id, type, title, payload, is_read, created_at "
        "FROM station_messages WHERE emp_id=:e ORDER BY msg_id DESC LIMIT 50",
        {"e": user["user_id"]})
    return {"messages": rows}


@router.post("/me/messages/{msg_id}/read")
async def read_message(msg_id: int, user: dict = Depends(get_current_user)):
    await execute("app",
                  "UPDATE station_messages SET is_read=1 WHERE msg_id=:m AND emp_id=:e",
                  {"m": msg_id, "e": user["user_id"]})
    return {"ok": True}


@router.post("/me/messages/read-all")
async def read_all_messages(user: dict = Depends(get_current_user)):
    await execute("app",
                  "UPDATE station_messages SET is_read=1 WHERE emp_id=:e",
                  {"e": user["user_id"]})
    return {"ok": True}


@router.get("/me/tickets")
async def my_tickets(user: dict = Depends(get_current_user)):
    """我的报修工单（走器材报修 Agent，与对话同一数据链路）。"""
    r = await call_agent(settings.equipment_repair_agent_url, "my_tickets", "我的工单",
                         employee={"id": user["user_id"], "role": user["role"]})
    tickets = ((r.get("result") or {}).get("data") or {}).get("tickets", []) \
        if r.get("state") == "completed" else []
    return {"tickets": tickets}
