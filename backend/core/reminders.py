# backend/core/reminders.py
# 站内提醒（APScheduler）：
#   1) 会议提醒：扫描今天 book_room 写审计 → 检测 15 分钟内即将开始的预订
#      → 用 OA MCP 复核"预订仍有效"（防已取消还提醒）→ 写 station_messages（按 booking_id 去重）
#   2) 工单超时：office_asset.repair_tickets 中超过 SLA 且未完成的 → 提醒报修人（按 ticket_no 去重）
#
# 设计口径：提醒基于「写操作审计 + 实时复核」，不复制业务数据；只写站内信，不做真 IM。

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import text

from backend.config import get_settings
from backend.core.logger import get_logger
from backend.core.mcp_client import call_mcp_tool
from backend.db.engine import execute, fetch_all

logger = get_logger(__name__)
settings = get_settings()


async def _message_exists(emp_id: str, msg_type: str, json_path: str, value: str) -> bool:
    rows = await fetch_all(
        "app",
        f"SELECT msg_id FROM station_messages WHERE emp_id=:e AND type=:t "
        f"AND JSON_UNQUOTE(JSON_EXTRACT(payload, '{json_path}'))=:v LIMIT 1",
        {"e": emp_id, "t": msg_type, "v": value})
    return bool(rows)


async def _booking_still_active(emp_id: str, booking_id: str) -> bool:
    """实时复核：该预订仍在 OA 的进行中列表里（防已取消/改期还提醒）。"""
    r = await call_mcp_tool(settings.oa_mcp_url, "list_my_bookings", {"employee_id": emp_id})
    if r.get("status") != "success":
        logger.warning("reminder.verify_unavailable", booking_id=booking_id)
        return False                      # 复核不了就不提醒（保守）
    bookings = (r.get("data") or {}).get("bookings", []) or []
    return any(b.get("booking_id") == booking_id for b in bookings)


async def run_meeting_reminders(lead_minutes: int | None = None) -> int:
    """会前提醒，返回新建消息数。"""
    lead = lead_minutes or settings.reminder_lead_minutes
    now = datetime.now()
    rows = await fetch_all("app", """
        SELECT actor, detail FROM write_audit
        WHERE action='book_room' AND result='ok' AND created_at >= CURDATE()
        ORDER BY id DESC LIMIT 200
    """)
    created = 0
    for r in rows:
        try:
            detail = r["detail"] or {}
            if isinstance(detail, str):
                detail = json.loads(detail)
            req = detail.get("request") or {}
            booking = ((detail.get("response") or {}).get("booking") or {})
            date_s = req.get("date") or booking.get("date")
            start_s = (req.get("start_time") or booking.get("start_time") or "")[:5]
            room = req.get("room_id") or booking.get("room_id") or ""
            booking_id = booking.get("booking_id")
            emp = r["actor"] or req.get("employee_id")
            if not (date_s and start_s and room and booking_id and emp):
                continue
            start_dt = datetime.fromisoformat(f"{date_s} {start_s}")
            if not (now <= start_dt <= now + timedelta(minutes=lead)):
                continue
            if await _message_exists(emp, "meeting_soon", "$.booking_id", booking_id):
                continue
            if not await _booking_still_active(emp, booking_id):
                continue
            await execute("app", """
                INSERT INTO station_messages (emp_id, type, title, payload)
                VALUES (:e, 'meeting_soon', :t, :p)
            """, {"e": emp,
                  "t": f"会议即将开始：{date_s} {start_s} {room}",
                  "p": json.dumps({"booking_id": booking_id, "room_id": room,
                                   "date": date_s, "start_time": start_s}, ensure_ascii=False)})
            created += 1
            logger.info("reminder.meeting_created", emp=emp, booking_id=booking_id, room=room)
        except Exception as e:  # noqa: BLE001
            logger.warning("reminder.meeting_skip", error=str(e)[:150])
    return created


async def run_ticket_reminders() -> int:
    """工单超时提醒，返回新建消息数。"""
    rows = await fetch_all("asset", """
        SELECT ticket_no, asset_id, fault_desc, reporter_id, sla_due_at
        FROM repair_tickets
        WHERE status IN ('open', 'in_progress')
          AND sla_due_at IS NOT NULL AND sla_due_at < NOW()
    """)
    created = 0
    for r in rows:
        try:
            tno, emp = r["ticket_no"], r["reporter_id"]
            if not (tno and emp):
                continue
            if await _message_exists(emp, "ticket_overdue", "$.ticket_no", tno):
                continue
            await execute("app", """
                INSERT INTO station_messages (emp_id, type, title, payload)
                VALUES (:e, 'ticket_overdue', :t, :p)
            """, {"e": emp,
                  "t": f"工单超时提醒：{tno}（{r.get('asset_id') or '未指明设备'}）已超过 SLA",
                  "p": json.dumps({"ticket_no": tno, "asset_id": r.get("asset_id"),
                                   "sla_due_at": str(r.get("sla_due_at"))}, ensure_ascii=False)})
            created += 1
            logger.info("reminder.ticket_created", emp=emp, ticket_no=tno)
        except Exception as e:  # noqa: BLE001
            logger.warning("reminder.ticket_skip", error=str(e)[:150])
    return created


def build_scheduler():
    """构建调度器（在网关 lifespan 中启动）。"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_meeting_reminders, "interval",
                      seconds=settings.reminder_interval_seconds,
                      id="meeting_reminders", max_instances=1)
    scheduler.add_job(run_ticket_reminders, "interval",
                      seconds=max(settings.reminder_interval_seconds, 60),
                      id="ticket_reminders", max_instances=1)
    return scheduler
