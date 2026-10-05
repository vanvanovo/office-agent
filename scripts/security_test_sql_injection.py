# scripts/security_test_sql_injection.py
# P7 安全验收：SQL 注入 / 越权 / 工具面 安全测试（对真实在跑的服务打）
#
#   1 注入 payload 扫描：器材 MCP `query_equipment`（白名单模板 + 参数绑定）
#   2 注入后数据库完整性：equipment 表仍在、条数不变
#   3 工具面：两个 MCP Server 都不暴露任何 `sql` 参数
#   4 写入面：book_room 的 room_id 注入不产生副作用
#   5 越权面：取消他人预订 / 催他人工单（复核 P2/P5 用例）
#
# 前置：Mock:8210 / OA MCP:8111 / 器材 MCP:8112 / 预订 Agent:5012 / 报修 Agent:5014 已启动
# 运行：python scripts/security_test_sql_injection.py

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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

PAYLOADS = [
    "' OR '1'='1",
    '" OR "1"="1',
    "'; DROP TABLE equipment; --",
    "%' UNION SELECT asset_id, name, category, brand, model, department, owner_id, status, borrowable FROM equipment --",
    "admin'--",
    "1; DELETE FROM equipment; --",
    "%",
    "_",
    "投影仪' OR 1=1 --",
    "A" * 2000,
]

passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


async def rpc(url: str, method: str, params: dict) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.mcp_shared_key:
        headers["X-MCP-Key"] = settings.mcp_shared_key
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        r = await c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                         headers=headers)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        return {"__error__": body["error"]}
    result = body.get("result", {})
    if "tools" in result:                      # tools/list：原样返回
        return body
    for item in result.get("content", []):     # tools/call：解析文本载荷
        txt = item.get("text", "") if isinstance(item, dict) else ""
        if txt:
            try:
                return json.loads(txt)
            except (json.JSONDecodeError, TypeError):
                return {"raw": txt}
    return {}


async def main() -> int:
    engine = create_async_engine(settings.mysql_asset_url)

    print("[1] 注入 payload 扫描（query_equipment.keyword）")
    for i, payload in enumerate(PAYLOADS):
        r = await rpc(settings.asset_mcp_url, "tools/call",
                      {"name": "query_equipment", "arguments": {"keyword": payload}})
        status = r.get("status")
        items = (r.get("data") or {}).get("items", []) if status == "success" else []
        # 参数绑定下这些 payload 只会当普通字面量：要么 0 条命中，要么是字面量恰好命中（如 "%"，LIKE 转义由驱动处理）
        ok = status == "success" and all(isinstance(x, dict) for x in items)
        check(f"payload#{i + 1} 被安全处理（{payload[:24]}…）", ok,
              f"status={status} items={len(items)} detail={str(r)[:150]}")

    print("[2] 注入后数据库完整性")
    async with engine.connect() as conn:
        total = (await conn.execute(text("SELECT COUNT(*) AS c FROM equipment"))).mappings().first()["c"]
        tables = (await conn.execute(text("SHOW TABLES"))).mappings().all()
    check("equipment 表存在且 32 条不变", total == 32, f"count={total}")
    check("repair_tickets 表未被删除", any("repair_tickets" in list(t.values()) for t in tables), str(tables))

    print("[3] 工具面：不暴露 sql 参数")
    for name, url in (("OA MCP", settings.oa_mcp_url), ("器材 MCP", settings.asset_mcp_url)):
        tools = (await rpc(url, "tools/list", {})).get("result", {}).get("tools", [])
        bad = [t["name"] for t in tools
               if "sql" in (t.get("inputSchema", {}).get("properties") or {})]
        check(f"{name} 无 sql 参数（{len(tools)} 个工具）", len(tools) >= 6 and not bad,
              f"工具数={len(tools)} 违规工具={bad}")

    print("[4] 写入面：book_room 的 room_id 注入无副作用")
    r = await rpc(settings.oa_mcp_url, "tools/call",
                  {"name": "book_room", "arguments": {
                      "room_id": "A-802' OR '1'='1", "date": TOMORROW,
                      "start_time": "19:00", "end_time": "20:00",
                      "employee_id": "E1001",
                      "idempotency_key": "sec-test-001"}})
    check("注入式 room_id 不落单（not_found/error）",
          r.get("status") in ("not_found", "error", "conflict"), str(r)[:200])
    async with engine.connect() as conn:
        cnt = (await conn.execute(text(
            "SELECT COUNT(*) AS c FROM office_oa.bookings WHERE room_id LIKE '%OR%'"
        ))).mappings().first()["c"]
    check("bookings 无脏数据", cnt == 0, f"count={cnt}")

    print("[5] 越权面（复核）")
    from backend.core.a2a_client import call_agent
    other_bid = f"BK{(date.today() + timedelta(days=1)).strftime('%Y%m%d')}-SEED02"  # 种子：E1004 的预订
    r = await call_agent(settings.meeting_book_agent_url, "cancel", "取消", employee={"id": "E1002"},
                         slots={"booking_id": other_bid})
    check("取消他人预订被拒", "只能" in ((r.get("result") or {}).get("answer", "")), str(r.get("result"))[:150])
    r = await call_agent(settings.equipment_repair_agent_url, "urge", "催单", employee={"id": "E1002"},
                         slots={"ticket_id": "RT00000000-XXXX"})
    ans = (r.get("result") or {}).get("answer", "")
    check("不存在工单催单被拒", r["state"] == "completed"
          and ("没找到" in ans or "不存在" in ans), ans[:150])

    await engine.dispose()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
