# scripts/check_reports.py
# P9a 验收：运营报表（仅 admin）
#   1 admin 登录 → 会议室利用率报表（10 间房、字段完整、区间内有占用）
#   2 admin → 报修热点报表（结构完整）
#   3 普通员工 → 403（越权）
#
# 前置：Mock:8210 / OA MCP:8111 / 器材 MCP:8112 / 网关:8010 已启动
# 运行：python scripts/check_reports.py

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

BASE = "http://127.0.0.1:8010/api/v1"
TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

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


async def main() -> int:
    admin = await login("A9001")
    emp = await login("E1001")
    params = {"from": TODAY, "to": TOMORROW}
    async with httpx.AsyncClient(timeout=30) as c:
        h_admin = {"Authorization": f"Bearer {admin}"}
        h_emp = {"Authorization": f"Bearer {emp}"}

        print("[1] 会议室利用率（admin）")
        r = await c.get(f"{BASE}/reports/rooms", params=params, headers=h_admin)
        rooms = r.json().get("rooms", [])
        check("200 且 10 间房", r.status_code == 200 and len(rooms) == 10, f"status={r.status_code} n={len(rooms)}")
        fields_ok = all(k in rooms[0] for k in ("room_id", "booking_count", "booked_hours")) if rooms else False
        check("字段完整", fields_ok, str(rooms[:1]))
        total_hours = sum(x.get("booked_hours", 0) for x in rooms)
        check("区间内有占用（种子预订）", total_hours > 0, f"total_hours={total_hours}")

        print("[2] 报修热点（admin）")
        r = await c.get(f"{BASE}/reports/repairs", params=params, headers=h_admin)
        body = r.json()
        check("200 且结构完整", r.status_code == 200 and "by_category" in body and "top_assets" in body,
              f"status={r.status_code} body={str(body)[:120]}")

        print("[3] 普通员工越权 → 403")
        r = await c.get(f"{BASE}/reports/rooms", params=params, headers=h_emp)
        check("员工访问报表被拒", r.status_code == 403, f"status={r.status_code}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
