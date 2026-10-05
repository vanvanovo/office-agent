# agents/meeting_query/server.py
# 会议室查询 Agent（A2A 独立服务 :5011）
#   能力：query（查可用会议室）/ my_bookings（我的预订）
#   数据链路：Agent → OA MCP（参数化工具）→ Mock OA（实时）
#
# 运行：python agents/meeting_query/server.py   → http://localhost:5011

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from python_a2a import A2AServer, AgentCard, AgentSkill, Task, run_server  # noqa: E402

from agents.common.contract import (  # noqa: E402
    TaskRequest,
    failed,
    render_result,
)
from agents.common.mcp_client import call_mcp_tool  # noqa: E402
from agents.common.runtime import AgentRuntime  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

AGENT_NAME = "MeetingQueryAgent"
PORT = 5011

CARD = AgentCard(
    name=AGENT_NAME,
    description="会议室查询助手：查询指定时段可用会议室、查询本人预订（实时数据来自 OA，不落库）",
    url=f"http://localhost:{PORT}",
    version="1.0.0",
    capabilities={"streaming": False, "memory": False},
    skills=[
        AgentSkill(name="query_availability",
                   description="查询指定日期时段内可用的会议室",
                   examples=["明天下午 3 点 A 栋有哪些空会议室", "8 楼 10 人以上会议室"]),
        AgentSkill(name="my_bookings",
                   description="查询本人进行中的会议室预订",
                   examples=["我明天有哪些会", "我的会议室预订"]),
    ],
)


def _fmt_rooms(rooms: list[dict]) -> str:
    if not rooms:
        return "该时段没有符合条件的可用会议室。"
    items = []
    for r in rooms:
        eq = "、".join(r.get("equipment") or [])
        items.append(f"{r['room_id']} {r['name']}（{r['capacity']}人" + (f"，{eq}" if eq else "") + "）")
    return f"共 {len(rooms)} 间可用：" + "；".join(items)


def _fmt_bookings(bookings: list[dict]) -> str:
    if not bookings:
        return "你当前没有进行中的会议室预订。"
    items = [f"{b['date']} {b['start_time']}-{b['end_time']} {b['room_id']}"
             f"（{b.get('purpose') or '未填用途'}）" for b in bookings]
    return f"你共有 {len(bookings)} 个预订：" + "；".join(items)


class MeetingQueryAgent(A2AServer):
    def __init__(self):
        super().__init__(agent_card=CARD)
        self.runtime = AgentRuntime()

    # ── 业务处理（统一返回 dict，由 render_result 转成 Task） ──

    async def _handle(self, req: TaskRequest) -> dict:
        action = (req.action or "query").lower()
        if action in ("query", "meeting_query", "availability"):
            return await self._query(req)
        if action in ("my_bookings", "my"):
            return await self._my_bookings(req)
        return {"state": "input_required",
                "clarify": f"暂不支持的查询类型：{action}。支持：查询可用会议室 / 我的预订。"}

    async def _query(self, req: TaskRequest) -> dict:
        slots = req.slots
        labels = {"date": "日期", "start_time": "开始时间", "end_time": "结束时间"}
        missing = [labels[k] for k in ("date", "start_time", "end_time") if not slots.get(k)]
        if missing:
            return {"state": "input_required",
                    "clarify": "请补充：" + "、".join(missing) + "。例如：明天 15:00-16:00。"}

        args = {"date": slots["date"], "start_time": slots["start_time"], "end_time": slots["end_time"]}
        if slots.get("building"):
            args["building"] = slots["building"]
        if slots.get("floor") not in (None, ""):
            args["floor"] = slots["floor"]
        if slots.get("capacity_min") not in (None, ""):
            args["capacity_min"] = slots["capacity_min"]

        res = await call_mcp_tool("list_available_rooms", args)
        if res.get("status") != "success":
            return {"state": "failed", "message": res.get("message", "查询失败")}

        data = res.get("data") or {}
        rooms = data.get("rooms", []) or []
        d, s, e = data.get("date", slots["date"]), data.get("start_time", slots["start_time"]), data.get("end_time", slots["end_time"])
        logger.info("meeting_query.answered", date=d, slot=f"{s}-{e}", rooms=len(rooms))
        return {"state": "completed",
                "answer": f"{d} {s}-{e} {_fmt_rooms(rooms)}",
                "data": {"rooms": rooms, "date": d, "start_time": s, "end_time": e}}

    async def _my_bookings(self, req: TaskRequest) -> dict:
        if not req.employee_id:
            return {"state": "input_required", "clarify": "请先登录（缺少工号）。"}
        res = await call_mcp_tool("list_my_bookings", {"employee_id": req.employee_id})
        if res.get("status") != "success":
            return {"state": "failed", "message": res.get("message", "查询失败")}
        bookings = (res.get("data") or {}).get("bookings", []) or []
        return {"state": "completed", "answer": _fmt_bookings(bookings),
                "data": {"bookings": bookings}}

    # ── A2A 同步入口 ──

    def handle_task(self, task: Task) -> Task:
        try:
            req = TaskRequest.parse(task)
            result = self.runtime.run(self._handle(req))
        except Exception as e:  # noqa: BLE001 —— 异常落到 FAILED，不炸服务
            logger.error("meeting_query.exception", error=str(e)[:300])
            return failed(task, message=f"会议室查询服务异常：{e}")
        return render_result(task, result)


if __name__ == "__main__":
    configure_logging()
    agent = MeetingQueryAgent()
    print(f"{AGENT_NAME} A2A → http://localhost:{PORT}")
    print(f"  skills: {[s.name for s in CARD.skills]}")
    run_server(agent, host="127.0.0.1", port=PORT)
