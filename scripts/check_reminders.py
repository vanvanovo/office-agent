# scripts/check_reminders.py
# P9b 验收：站内提醒（会前提醒 + 工单超时）
#   1 预订一个 10 分钟后开始的会议 → 触发会前提醒（新建 1 条）
#   2 再跑一次 → 去重（0 条）
#   3 站内信内容/未读状态正确
#   4 工单超时提醒（种子含超 SLA 工单）→ 新建 ≥1 条；复跑去重
#
# 前置：Mock:8210 / OA MCP:8111 / 预订 Agent:5012 / MySQL 已就绪
# 运行：python scripts/check_reminders.py

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import httpx  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.a2a_client import call_agent  # noqa: E402
from backend.core.reminders import run_meeting_reminders, run_ticket_reminders  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


async def main() -> int:
    # ⓿ 清理上一轮"提醒验收"预订（保持脚本幂等）
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        try:
            r = await c.get(f"{settings.mock_internal_url}/oa/bookings/my",
                            params={"employee_id": "E1001"})
            for b in r.json().get("bookings", []):
                if b.get("purpose") == "提醒验收":
                    await c.post(f"{settings.mock_internal_url}/oa/bookings/{b['booking_id']}/cancel",
                                 json={"employee_id": "E1001"})
        except Exception:  # noqa: BLE001
            pass

    # ① 预订一个 10 分钟后开始的会议
    start_dt = (datetime.now() + timedelta(minutes=10)).replace(second=0, microsecond=0)
    end_dt = start_dt + timedelta(minutes=30)
    d = start_dt.strftime("%Y-%m-%d")
    s = start_dt.strftime("%H:%M")
    e = end_dt.strftime("%H:%M")
    print(f"[0] 预订 B-601 {d} {s}-{e}（10 分钟后开始）")
    r = await call_agent(settings.meeting_book_agent_url, "book", "订一个马上开始的会",
                         employee={"id": "E1001", "role": "employee"},
                         slots={"room_id": "B-601", "date": d, "start_time": s, "end_time": e,
                                "purpose": "提醒验收", "idempotency_key": f"remind-{uuid.uuid4().hex[:8]}"})
    booking = ((r.get("result") or {}).get("data") or {}).get("booking") or {}
    bid = booking.get("booking_id")
    check("预订成功", r["state"] == "completed" and bool(bid),
          f"state={r['state']} result={str(r.get('result'))[:150]}")
    if not bid:
        print(f"\n结果：{passed} 通过 / {failed} 失败")
        return 1

    print("[1] 会前提醒（第一次运行）")
    m1 = await run_meeting_reminders()
    check("新建会前提醒 ≥1", m1 >= 1, f"created={m1}")

    print("[2] 去重（第二次运行）")
    m2 = await run_meeting_reminders()
    check("重复运行不重复提醒", m2 == 0, f"created={m2}")

    print("[3] 站内信内容与未读状态")
    engine = create_async_engine(settings.mysql_app_url)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("""
                SELECT title, is_read FROM station_messages
                WHERE emp_id='E1001' AND type='meeting_soon'
                  AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.booking_id')) = :b
                ORDER BY msg_id DESC LIMIT 1
            """), {"b": bid})).mappings().first()
    finally:
        await engine.dispose()
    check("站内信存在且未读", row is not None and row["is_read"] == 0, str(dict(row) if row else None))
    if row:
        print(f"      标题：{row['title']}")

    print("[4] 工单超时提醒（自建一条超 SLA 工单，保证幂等）")
    import uuid as _uuid
    from backend.core.mcp_client import call_mcp_tool
    r = await call_mcp_tool(settings.asset_mcp_url, "create_repair_ticket",
                            {"fault_desc": "提醒验收用故障", "asset_id": "P004",
                             "urgency": "low", "reporter_id": "E1001",
                             "idempotency_key": f"remind-ticket-{_uuid.uuid4().hex[:8]}"})
    new_ticket = ((r.get("data") or {}).get("ticket") or {}).get("ticket_no")
    engine2 = create_async_engine(settings.mysql_asset_url)
    try:
        async with engine2.begin() as conn:
            await conn.execute(text(
                "UPDATE repair_tickets SET sla_due_at = NOW() - INTERVAL 2 HOUR WHERE ticket_no=:t"),
                {"t": new_ticket})
    finally:
        await engine2.dispose()
    t1 = await run_ticket_reminders()
    check("新建工单超时提醒 ≥1", t1 >= 1, f"created={t1} ticket={new_ticket}")
    t2 = await run_ticket_reminders()
    check("重复运行不重复提醒", t2 == 0, f"created={t2}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
