# scripts/check_recurring.py
# P9c 验收：周期会议（每周固定时段，订 N 周）
#   1 直接 A2A：B-601 每周X 13:00-13:30 订 4 周 → 成功 4/4
#   2 重复预订同参数（另一员工）→ 全部冲突 0/4（清单返回）
#   3 缺 weekday → 追问
#   4 网关 SSE 全链路：「每周X上午10点到11点，帮我订4周B-601会议室」
#   5 清理：删除测试产生的系列预订
#
# 前置：Mock:8210 / OA MCP:8111 / 预订 Agent:5012 / 网关:8010 已启动
# 运行：python scripts/check_recurring.py

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.a2a_client import call_agent  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
BASE = "http://127.0.0.1:8010/api/v1"
TOMORROW = date.today() + timedelta(days=1)
WD = TOMORROW.isoweekday()
WD_NAME = "一二三四五六日"[WD - 1]
EMP1 = {"id": "E1001", "role": "employee"}
EMP2 = {"id": "E1002", "role": "employee"}
passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


async def login(emp_id: str) -> str:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{BASE}/auth/login", json={"emp_id": emp_id})
        r.raise_for_status()
        return r.json()["access_token"]


async def chat(session_id: str, message: str, token: str) -> str:
    tokens = ""
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream("POST", f"{BASE}/chat/stream",
                            json={"session_id": session_id, "message": message},
                            headers={"Authorization": f"Bearer {token}"}) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                evt = json.loads(line[5:].strip())
                if evt.get("type") == "token":
                    tokens += evt.get("content", "")
    return tokens


async def main() -> int:
    print(f"[0] 取明天（{TOMORROW.isoformat()}）的星期：每周{WD_NAME}")

    print("[1] 直接 A2A 周期预订（4 周）")
    r = await call_agent(settings.meeting_book_agent_url, "recur", "周期预订",
                         employee=EMP1,
                         slots={"room_id": "B-601", "weekday": WD,
                                "start_time": "13:00", "end_time": "13:30", "weeks": 4})
    series = ((r.get("result") or {}).get("data") or {}).get("series") or {}
    check("成功 4/4", r["state"] == "completed" and series.get("booked") == 4
          and series.get("conflict") == 0, f"state={r['state']} series={str(series)[:180]}")

    print("[2] 同参数重复预订（E1002）→ 全部冲突")
    r = await call_agent(settings.meeting_book_agent_url, "recur", "周期预订",
                         employee=EMP2,
                         slots={"room_id": "B-601", "weekday": WD,
                                "start_time": "13:00", "end_time": "13:30", "weeks": 4})
    series2 = ((r.get("result") or {}).get("data") or {}).get("series") or {}
    check("冲突 4/4 且清单返回", series2.get("booked") == 0 and series2.get("conflict") == 4
          and len(series2.get("results") or []) == 4, str(series2)[:180])

    print("[3] 缺 weekday → 追问")
    r = await call_agent(settings.meeting_book_agent_url, "recur", "周期预订",
                         employee=EMP1,
                         slots={"room_id": "B-601", "start_time": "15:00",
                                "end_time": "16:00", "weeks": 4})
    check("返回追问（每周几）", r["state"] == "input_required"
          and "周几" in r.get("message", ""), f"state={r['state']} msg={r.get('message')}")

    print("[4] 网关 SSE 全链路")
    token = await login("E1001")
    text_ = await chat(f"recur-{WD}", f"每周{WD_NAME}上午10点到11点，帮我订4周B-601会议室", token)
    check("回答含周期预订结果", "周期预订" in text_ and "成功" in text_, text_[:200])

    print("[5] 清理测试系列预订")
    engine = create_async_engine(settings.mysql_oa_url)
    try:
        async with engine.begin() as conn:
            res = await conn.execute(text("DELETE FROM bookings WHERE series_id IS NOT NULL"))
    finally:
        await engine.dispose()
    print(f"      deleted series bookings: {res.rowcount}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
