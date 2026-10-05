# scripts/check_oa_mcp.py
# P2 验收：OA MCP 工具服务
#   1 tools/list 六个工具
#   2 读工具：list_available_rooms / get_room_info / list_my_bookings
#   3 写工具：book_room（幂等复放 + 冲突）→ reschedule_booking → cancel_booking（越权拒绝 + 本人成功）
#   4 审计落库断言（office_app.write_audit）
#   5 鉴权：无 Key / 错误 Key → 401
#
# 前置：Mock 内部系统(:8210) 与 OA MCP(:8111) 已启动
# 运行：python scripts/check_oa_mcp.py

import asyncio
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
MCP_URL = settings.oa_mcp_url                      # http://localhost:8111/mcp
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


async def rpc(method: str, params: dict | None = None, with_key: bool = True) -> httpx.Response:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if with_key and settings.mcp_shared_key:
        headers["X-MCP-Key"] = settings.mcp_shared_key
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    async with httpx.AsyncClient(timeout=15, trust_env=False) as c:
        return await c.post(MCP_URL, json=payload, headers=headers)


async def call_tool(name: str, arguments: dict) -> dict:
    resp = await rpc("tools/call", {"name": name, "arguments": arguments})
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        return {"__rpc_error__": data["error"]}
    content = data.get("result", {}).get("content", [])
    for item in content:
        text_ = item.get("text", "") if isinstance(item, dict) else ""
        if text_:
            try:
                return json.loads(text_)
            except (json.JSONDecodeError, TypeError):
                return {"raw": text_}
    return {"raw": ""}


async def main() -> int:
    print("[1] tools/list")
    resp = await rpc("tools/list")
    tools = [t["name"] for t in resp.json().get("result", {}).get("tools", [])]
    expected = {"list_available_rooms", "get_room_info", "book_room",
                "reschedule_booking", "cancel_booking", "list_my_bookings",
                "book_recurring", "get_room_usage_report"}
    check("8 个工具齐全", set(tools) == expected, f"实际={tools}")
    check("工具描述非空", all(t.get("description") for t in resp.json()["result"]["tools"]))

    print("[2] 读工具")
    r = await call_tool("list_available_rooms", {"date": TOMORROW, "start_time": "15:00",
                                                 "end_time": "16:00", "building": "A"})
    ids = [x["room_id"] for x in r.get("data", {}).get("rooms", [])]
    check("可用查询：A-801 在列表、A-501 不在", "A-801" in ids and "A-501" not in ids, str(r)[:200])
    r = await call_tool("get_room_info", {"room_id": "A-801"})
    check("房间详情：容量 12", r.get("data", {}).get("capacity") == 12, str(r)[:200])
    r = await call_tool("list_my_bookings", {"employee_id": "E1002"})
    check("我的预订包含种子单", any("SEED" in b["booking_id"]
                                    for b in r.get("data", {}).get("bookings", [])), str(r)[:200])

    print("[3] 写工具：预订 + 幂等 + 冲突")
    idem = f"mcp-check-{uuid.uuid4().hex[:8]}"
    args = {"room_id": "B-601", "date": TOMORROW, "start_time": "13:00", "end_time": "14:00",
            "employee_id": "E1001", "purpose": "MCP 验收", "idempotency_key": idem}
    r1 = await call_tool("book_room", args)
    booking_id = r1.get("data", {}).get("booking", {}).get("booking_id")
    check("预订成功", r1.get("status") == "success" and bool(booking_id), str(r1)[:200])
    r2 = await call_tool("book_room", args)
    check("幂等复放返回同一单",
          r2.get("data", {}).get("booking", {}).get("booking_id") == booking_id, str(r2)[:200])
    r3 = await call_tool("book_room", {**args, "idempotency_key": f"mcp-check-{uuid.uuid4().hex[:8]}"})
    check("同时段冲突返回 conflict", r3.get("status") == "conflict", str(r3)[:200])

    print("[4] 改期（先定新再放旧）+ 取消（越权拒绝 → 本人成功）")
    r4 = await call_tool("reschedule_booking", {"booking_id": booking_id, "new_date": TOMORROW,
                                                "new_start_time": "14:00", "new_end_time": "15:00",
                                                "employee_id": "E1001",
                                                "idempotency_key": f"mcp-check-{uuid.uuid4().hex[:8]}"})
    new_id = r4.get("data", {}).get("booking", {}).get("booking_id")
    check("改期成功返回新单", r4.get("status") == "success" and new_id != booking_id, str(r4)[:200])
    r5 = await call_tool("list_my_bookings", {"employee_id": "E1001"})
    my_ids = [b["booking_id"] for b in r5.get("data", {}).get("bookings", [])]
    check("列表：新单在、旧单不在", new_id in my_ids and booking_id not in my_ids, str(my_ids))
    r6 = await call_tool("cancel_booking", {"booking_id": new_id, "employee_id": "E1002"})
    check("他人取消返回 rejected", r6.get("status") == "rejected", str(r6)[:200])
    r7 = await call_tool("cancel_booking", {"booking_id": new_id, "employee_id": "E1001"})
    check("本人取消成功", r7.get("status") == "success", str(r7)[:200])

    print("[5] 审计落库断言")
    engine = create_async_engine(settings.mysql_app_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT action, result, COUNT(*) AS c FROM write_audit GROUP BY action, result"
            ))).mappings().all()
    finally:
        await engine.dispose()
    audit = {(r["action"], r["result"]): r["c"] for r in rows}
    check("book ok 审计 ≥1", audit.get(("book_room", "ok"), 0) >= 1, str(audit))
    check("book conflict 审计 ≥1", audit.get(("book_room", "conflict"), 0) >= 1, str(audit))
    check("reschedule ok 审计 ≥1", audit.get(("reschedule_booking", "ok"), 0) >= 1, str(audit))
    check("cancel rejected+ok 审计", audit.get(("cancel_booking", "rejected"), 0) >= 1
          and audit.get(("cancel_booking", "ok"), 0) >= 1, str(audit))

    print("[6] 鉴权")
    r8 = await rpc("tools/list", with_key=False)
    check("无 Key → 401", r8.status_code == 401, f"status={r8.status_code}")
    if settings.mcp_shared_key:
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "X-MCP-Key": "wrong-key"}
        async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
            r9 = await c.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                             "params": {}}, headers=headers)
        check("错误 Key → 401", r9.status_code == 401, f"status={r9.status_code}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
