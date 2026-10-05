# backend/api/v1/chat.py
# Web 对话入口（SSE 流式）：把「对话内核」的事件翻译成 SSE 协议。
#
# 事件协议（与前端 useSSEChat 解析对齐）：
#   {"type":"routing","intents":[...],"slots":{...},"need_clarify":bool}
#   {"type":"progress","stage":"...","label":"..."}
#   {"type":"card","card_type":"...","payload":{...}}
#   {"type":"token","content":"..."}
#   {"type":"meta","elapsed_ms":int,"degraded":bool,"sources":[]}
#   {"type":"done"} / {"type":"error","message":"..."}

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.core.conversation import run_turn_events
from backend.core.session import get_session
from backend.dependencies import get_current_user

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    message: str


def _evt(type_: str, **kw) -> str:
    return json.dumps({"type": type_, **kw}, ensure_ascii=False)


async def _stream_text(text: str, size: int = 6, delay: float = 0.02):
    for i in range(0, len(text), size):
        yield text[i:i + size]
        await asyncio.sleep(delay)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    session = get_session(req.session_id, user["user_id"],
                          user.get("role", "employee"), user.get("name", ""))
    text = (req.message or "").strip()

    async def event_gen():
        try:
            async for e in run_turn_events(session, text):
                kind = e.get("kind")
                if kind == "routing":
                    yield {"data": _evt("routing", intents=e["intents"], slots=e["slots"],
                                        need_clarify=e["need_clarify"])}
                elif kind == "progress":
                    yield {"data": _evt("progress", stage=e["stage"], label=e["label"])}
                elif kind == "card":
                    yield {"data": _evt("card", card_type=e["card_type"], payload=e["payload"])}
                elif kind == "reply":
                    async for chunk in _stream_text(e["reply"]):
                        yield {"data": _evt("token", content=chunk)}
                elif kind == "meta":
                    yield {"data": _evt("meta", elapsed_ms=e["elapsed_ms"],
                                        degraded=e["degraded"], sources=[])}
                elif kind == "error":
                    yield {"data": _evt("error", message=e["message"])}
        finally:
            yield {"data": _evt("done")}

    return EventSourceResponse(event_gen())
