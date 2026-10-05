# scripts/check_asset_mcp.py
# P5 验收（1/2）：器材 MCP 工具服务
#   1 tools/list 六个工具
#   2 查询：类别/关键词/只看可借（静态+动态融合）
#   3 详情：动态状态（持有人/可借）
#   4 报修：创建 + 幂等复放 + 进度查询（SLA）
#   5 我的工单 / 催单（越权拒绝 + 本人成功）
#   6 审计落库 + X-MCP-Key 鉴权
#
# 前置：Mock:8210 / Asset MCP:8112 已启动，且已跑过 sync_assets.py
# 运行：python scripts/check_asset_mcp.py

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
MCP_URL = settings.asset_mcp_url

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
    for item in data.get("result", {}).get("content", []):
        txt = item.get("text", "") if isinstance(item, dict) else ""
        if txt:
            try:
                return json.loads(txt)
            except (json.JSONDecodeError, TypeError):
                return {"raw": txt}
    return {"raw": ""}


async def main() -> int:
    print("[1] tools/list")
    resp = await rpc("tools/list")
    tools = [t["name"] for t in resp.json().get("result", {}).get("tools", [])]
    expected = {"query_equipment", "get_equipment", "create_repair_ticket",
                "query_repair_ticket", "list_my_tickets", "urge_repair_ticket",
                "get_repair_hotspots"}
    check("7 个工具齐全", set(tools) == expected, f"实际={tools}")

    print("[2] 查询器材（静态+动态融合）")
    r = await call_tool("query_equipment", {"category": "投影仪"})
    items = (r.get("data") or {}).get("items", [])
    by_id = {i["asset_id"]: i for i in items}
    check("投影仪 5 台", len(items) == 5 and "P001" in by_id, f"total={len(items)}")
    check("P001 已借出 / P003 可借",
          by_id.get("P001", {}).get("borrowable_now") is False
          and by_id.get("P003", {}).get("borrowable_now") is True,
          str({k: v.get("borrowable_now") for k, v in by_id.items()}))
    r = await call_tool("query_equipment", {"keyword": "打印机"})
    check("关键词命中打印机 2 台", len((r.get("data") or {}).get("items", [])) == 2, str(r)[:150])
    r = await call_tool("query_equipment", {"category": "投影仪", "only_borrowable": True})
    check("只看可借 → 3 台", len((r.get("data") or {}).get("items", [])) == 3, str(r)[:150])

    print("[3] 详情")
    r = await call_tool("get_equipment", {"asset_id": "P001"})
    check("P001 持有人=李四", (r.get("data") or {}).get("holder") == "李四", str(r)[:150])

    print("[4] 报修 + 幂等 + 进度")
    idem = f"asset-check-{uuid.uuid4().hex[:8]}"
    args = {"fault_desc": "画面偏色", "asset_id": "P004", "urgency": "high",
            "reporter_id": "E1001", "idempotency_key": idem}
    r1 = await call_tool("create_repair_ticket", args)
    ticket = (r1.get("data") or {}).get("ticket", {})
    ticket_no = ticket.get("ticket_no")
    check("报修成功且带工单号与 SLA", r1.get("status") == "success" and bool(ticket_no)
          and bool(ticket.get("sla_due_at")), str(r1)[:200])
    r2 = await call_tool("create_repair_ticket", args)
    check("幂等复放返回同一工单",
          (r2.get("data") or {}).get("ticket", {}).get("ticket_no") == ticket_no, str(r2)[:200])
    r3 = await call_tool("query_repair_ticket", {"ticket_id": ticket_no})
    check("进度查询 open", (r3.get("data") or {}).get("ticket", {}).get("status") == "open", str(r3)[:150])

    print("[5] 我的工单 / 催单")
    r4 = await call_tool("list_my_tickets", {"reporter_id": "E1001"})
    check("我的工单包含新单", ticket_no in [t["ticket_no"] for t in (r4.get("data") or {}).get("tickets", [])],
          str(r4)[:150])
    r5 = await call_tool("urge_repair_ticket", {"ticket_id": ticket_no, "reporter_id": "E1002"})
    check("越权催单被拒", r5.get("status") == "rejected", str(r5)[:150])
    r6 = await call_tool("urge_repair_ticket", {"ticket_id": ticket_no, "reporter_id": "E1001"})
    check("本人催单成功且记录原因为空", r6.get("status") == "success", str(r6)[:150])

    print("[6] 审计与鉴权")
    engine = create_async_engine(settings.mysql_app_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT action, result, COUNT(*) AS c FROM write_audit GROUP BY action, result"
            ))).mappings().all()
    finally:
        await engine.dispose()
    audit = {(r["action"], r["result"]): r["c"] for r in rows}
    check("报修 ok 审计", audit.get(("create_repair_ticket", "ok"), 0) >= 1, str(audit))
    check("催单 rejected+ok 审计", audit.get(("urge_repair_ticket", "rejected"), 0) >= 1
          and audit.get(("urge_repair_ticket", "ok"), 0) >= 1, str(audit))
    r7 = await rpc("tools/list", with_key=False)
    check("无 Key → 401", r7.status_code == 401, f"status={r7.status_code}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
