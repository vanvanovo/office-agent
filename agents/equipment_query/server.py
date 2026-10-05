# agents/equipment_query/server.py
# 器材查询 Agent（A2A 独立服务 :5013）
#   能力：query —— 查器材台账（静态属性 + 实时可借状态）
#   数据链路：Agent → 器材 MCP（参数化工具，:8112）→ office_asset + Mock 资产系统
#
# 运行：python agents/equipment_query/server.py   → http://localhost:5013

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from python_a2a import A2AServer, AgentCard, AgentSkill, Task, run_server  # noqa: E402

from agents.common.contract import TaskRequest, failed, render_result  # noqa: E402
from agents.common.mcp_client import call_mcp_tool  # noqa: E402
from agents.common.runtime import AgentRuntime  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)
settings = get_settings()

AGENT_NAME = "EquipmentQueryAgent"
PORT = 5013

CARD = AgentCard(
    name=AGENT_NAME,
    description="器材查询助手：按类别/关键词/部门查台账，可借状态实时来自资产系统",
    url=f"http://localhost:{PORT}",
    version="1.0.0",
    capabilities={"streaming": False, "memory": False},
    skills=[
        AgentSkill(name="query_equipment",
                   description="查询器材台账与可借状态",
                   examples=["设计部还有几台投影仪可以借", "查一下打印机在哪"]),
    ],
)

STATUS_LABEL = {"in_stock": "在库", "issued_to_staff": "已领用"}


def _fmt_item(i: dict) -> str:
    state = "可借" if i.get("borrowable_now") else ("已借出" if i.get("holder") else STATUS_LABEL.get(i.get("status"), "不可借"))
    extra = f"，{i['holder']} 借出" if (i.get("holder") and not i.get("borrowable_now")) else ""
    return f"{i['asset_id']} {i['name']}（{i.get('department') or '—'}，{state}{extra}）"


class EquipmentQueryAgent(A2AServer):
    def __init__(self):
        super().__init__(agent_card=CARD)
        self.runtime = AgentRuntime()

    async def _handle(self, req: TaskRequest) -> dict:
        action = (req.action or "query").lower()
        if action not in ("query", "equipment_query", "availability"):
            return {"state": "input_required",
                    "clarify": f"暂不支持的查询类型：{action}。支持：查询器材台账。"}

        slots = req.slots
        args: dict = {}
        for k in ("category", "keyword", "department", "status"):
            if slots.get(k) not in (None, ""):
                args[k] = slots[k]
        if slots.get("only_borrowable") in (True, "true", "True", 1, "1"):
            args["only_borrowable"] = True

        res = await call_mcp_tool("query_equipment", args, server_url=settings.asset_mcp_url)
        if res.get("status") != "success":
            return {"state": "failed", "message": res.get("message", "器材查询失败")}

        data = res.get("data") or {}
        items = data.get("items", []) or []
        dept_note = ""

        # 部门是「归属部门」的软过滤：按部门查不到时，全公司范围兜底并说明
        if not items and args.get("department"):
            fallback_args = {k: v for k, v in args.items() if k != "department"}
            res2 = await call_mcp_tool("query_equipment", fallback_args, server_url=settings.asset_mcp_url)
            items2 = ((res2.get("data") or {}).get("items") or []) if res2.get("status") == "success" else []
            if items2:
                items = items2
                dept_note = f"按归属部门「{args['department']}」没有匹配，以下是全公司范围："

        if not items:
            hint = "没有找到匹配的器材，可以换个关键词或类别试试。"
            return {"state": "completed", "answer": hint, "data": {"equipment": [], "total": 0}}

        borrowable = [i for i in items if i.get("borrowable_now")]
        head = f"共找到 {len(items)} 件器材"
        if args.get("only_borrowable"):
            head += f"（可借 {len(borrowable)} 件）"
        else:
            head += f"，其中当前可借 {len(borrowable)} 件"
        shown = items[:10]
        lines = "；".join(_fmt_item(i) for i in shown)
        tail = "。" if len(items) <= 10 else f"。（仅列出前 10 件，共 {len(items)} 件）"
        answer = f"{dept_note}{head}：{lines}{tail}"
        logger.info("equipment_query.answered", total=len(items), borrowable=len(borrowable))
        return {"state": "completed", "answer": answer,
                "data": {"equipment": items, "total": len(items)}}

    def handle_task(self, task: Task) -> Task:
        try:
            req = TaskRequest.parse(task)
            result = self.runtime.run(self._handle(req))
        except Exception as e:  # noqa: BLE001
            logger.error("equipment_query.exception", error=str(e)[:300])
            return failed(task, message=f"器材查询服务异常：{e}")
        return render_result(task, result)


if __name__ == "__main__":
    configure_logging()
    agent = EquipmentQueryAgent()
    print(f"{AGENT_NAME} A2A → http://localhost:{PORT}")
    print(f"  skills: {[s.name for s in CARD.skills]}")
    run_server(agent, host="127.0.0.1", port=PORT)
