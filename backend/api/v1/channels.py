# backend/api/v1/channels.py
# IM 渠道适配（Mock 钉钉）：模拟「钉钉机器人回调 → 验签 → staffId 映射工号
# → 统一消息格式 → 同一对话内核 → 机器人回包」的完整链路。
#
# 生产形态（方案 §11.2）：用 dingtalk-stream SDK 长连接（内网免公网 IP），
# 验签后把 staffId 映射成工号，转成统一消息格式走同一条主链路；
# 回复用互动卡片更新模拟流式。本模块只做「协议翻译」，业务零改动。

from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.core.conversation import run_turn_events
from backend.core.logger import get_logger
from backend.core.session import get_session
from backend.db.engine import fetch_one

logger = get_logger(__name__)
router = APIRouter()
settings = get_settings()


class MockDingTalkWebhook(BaseModel):
    staff_id: str                       # 钉钉 staffId（演示：直接用工号）
    text: str
    conversation_id: str | None = None  # 群/单聊会话 id
    signature: str | None = None        # 模拟验签：md5(staff_id:text:key)[:8]


def _expected_signature(staff_id: str, text: str) -> str:
    raw = f"{staff_id}:{text}:{settings.mcp_shared_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


@router.post("/channels/mock-dingtalk/webhook")
async def mock_dingtalk_webhook(req: MockDingTalkWebhook):
    """模拟钉钉回调（演示用，不需要 JWT；真实环境为钉钉验签）。"""
    # ① 验签（提供 signature 才校验，便于本地演示）
    if req.signature and req.signature != _expected_signature(req.staff_id, req.text):
        logger.warning("channel.signature_invalid", staff_id=req.staff_id)
        raise HTTPException(401, "签名校验失败")

    # ② 身份映射：staffId → 工号（生产：查钉钉通讯录/企微 SSO 映射表）
    emp = await fetch_one(
        "app",
        "SELECT emp_id, name, role FROM employees WHERE emp_id=:e",
        {"e": req.staff_id.strip()})
    if not emp:
        return {"msgtype": "text",
                "text": {"content": "你的 IM 账号尚未绑定公司工号，请联系 IT（分机 8000）。"},
                "cards": [], "meta": {}}

    # ③ 统一消息格式 → 同一对话内核（与 Web 入口同链路）
    conv_id = req.conversation_id or req.staff_id
    session = get_session(f"dingtalk:{conv_id}", emp["emp_id"], emp["role"], emp["name"])

    reply, cards, meta = "", [], {}
    async for e in run_turn_events(session, req.text.strip()):
        if e.get("kind") == "reply":
            reply = e["reply"]
        elif e.get("kind") == "card":
            cards.append({"type": e["card_type"], "payload": e["payload"]})
        elif e.get("kind") == "meta":
            meta = {"elapsed_ms": e["elapsed_ms"], "degraded": e["degraded"]}
        elif e.get("kind") == "error":
            reply = reply or e["message"]

    # ④ 钉钉风格回包（生产：调钉钉消息回复接口 / 互动卡片更新）
    logger.info("channel.replied", staff_id=emp["emp_id"], cards=len(cards))
    return {
        "msgtype": "markdown",
        "markdown": {"title": "办公助手", "text": reply or "（本轮无回复内容）"},
        "cards": cards,                 # 演示：卡片随回包返回
        "meta": meta,
        "emp": {"emp_id": emp["emp_id"], "name": emp["name"], "role": emp["role"]},
    }
