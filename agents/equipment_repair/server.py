# agents/equipment_repair/server.py
# 器材报修 Agent（A2A 独立服务 :5014）
#   能力：repair（报修）/ progress（进度）/ my_tickets（我的工单）/ urge（催单）
#   数据链路：Agent → 器材 MCP（:8112）→ office_asset.repair_tickets
#   设计：设备模糊时自动按关键词解析——唯一命中直接用、多个命中列候选追问、没有命中要编号
#
# 运行：python agents/equipment_repair/server.py   → http://localhost:5014

from __future__ import annotations

import json
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

AGENT_NAME = "EquipmentRepairAgent"
PORT = 5014
URGENCY_LABEL = {"high": "紧急（4 小时内）", "medium": "一般（24 小时内）", "low": "不紧急（72 小时内）"}
STATUS_LABEL = {"open": "待处理", "in_progress": "处理中", "done": "已完成", "closed": "已关闭"}

CARD = AgentCard(
    name=AGENT_NAME,
    description="器材报修助手：报修（设备模糊时列出候选让你确认）/ 查进度 / 我的工单 / 催单",
    url=f"http://localhost:{PORT}",
    version="1.0.0",
    capabilities={"streaming": False, "memory": False},
    skills=[
        AgentSkill(name="create_repair",
                   description="创建报修工单（设备模糊时会列出候选让你确认）",
                   examples=["笔记本屏幕碎了帮我报修", "P004 投影仪画面偏色，急"]),
        AgentSkill(name="ticket_progress",
                   description="查询工单进度 / 我的工单",
                   examples=["我那个报修处理得怎么样了", "我的工单"]),
        AgentSkill(name="urge_ticket",
                   description="催单（仅本人）",
                   examples=["帮我催一下那个工单"]),
    ],
)


def _ticket_line(t: dict, with_reporter: bool = False) -> str:
    line = (f"{t['ticket_no']}：{t.get('asset_id') or '（未指明设备）'} "
            f"{t.get('fault_desc') or ''}（{STATUS_LABEL.get(t.get('status'), t.get('status'))}"
            f"，SLA {t.get('sla_due_at') or '—'}）")
    if t.get("urged_at"):
        line += "【已催】"
    if with_reporter:
        line += f" 报修人 {t.get('reporter_id')}"
    return line


class EquipmentRepairAgent(A2AServer):
    def __init__(self):
        super().__init__(agent_card=CARD)
        self.runtime = AgentRuntime()

    # ── 路由 ──

    async def _handle(self, req: TaskRequest) -> dict:
        action = (req.action or "repair").lower()
        if action in ("repair", "equipment_repair"):
            return await self._repair(req)
        if action in ("progress", "status"):
            return await self._progress(req)
        if action in ("my_tickets", "list"):
            return await self._my_tickets(req)
        if action in ("urge", "urge_ticket"):
            return await self._urge(req)
        return {"state": "input_required",
                "clarify": f"暂不支持的报修操作：{action}。支持：报修 / 查进度 / 我的工单 / 催单。"}

    # ── 报修 ──

    async def _resolve_asset(self, slots: dict) -> tuple[str | None, dict | None]:
        """解析设备：直接给编号 → 校验；给关键词 → 唯一命中采用 / 多个返回候选 / 无命中返回空。"""
        asset_id = str(slots.get("asset_id") or "").strip()
        if asset_id:
            r = await call_mcp_tool("get_equipment", {"asset_id": asset_id},
                                    server_url=settings.asset_mcp_url)
            if r.get("status") == "success":
                return asset_id, None
            return None, {"kind": "not_found", "asset_id": asset_id}

        keyword = str(slots.get("keyword") or "").strip()
        if not keyword:
            return None, {"kind": "no_hint"}
        r = await call_mcp_tool("query_equipment", {"keyword": keyword},
                                server_url=settings.asset_mcp_url)
        items = ((r.get("data") or {}).get("items") or []) if r.get("status") == "success" else []
        if len(items) == 1:
            return items[0]["asset_id"], None
        if len(items) == 0:
            return None, {"kind": "no_match", "keyword": keyword}
        return None, {"kind": "ambiguous", "candidates": items}

    async def _repair(self, req: TaskRequest) -> dict:
        slots = req.slots
        fault = str(slots.get("fault_desc") or "").strip()
        if not fault:
            return {"state": "input_required", "clarify": "请描述一下故障现象，例如：屏幕碎裂 / 无法开机。"}
        if not req.employee_id:
            return {"state": "input_required", "clarify": "请先登录（缺少工号）。"}

        asset_id, err = await self._resolve_asset(slots)
        if err:
            if err["kind"] == "ambiguous":
                cands = "、".join(f"{i['asset_id']}（{i['name']}，{i.get('department') or '—'}）"
                                  for i in err["candidates"][:5])
                return {"state": "input_required",
                        "clarify": f"找到多台匹配设备：{cands}。请告诉我要报修哪一台（给资产编号）。",
                        "data": {"equipment": err["candidates"]}}
            if err["kind"] == "not_found":
                return {"state": "input_required",
                        "clarify": f"没找到资产编号 {err['asset_id']}，请确认编号是否正确。"}
            if err["kind"] == "no_match":
                return {"state": "input_required",
                        "clarify": f"没找到与「{err['keyword']}」匹配的设备，请提供资产编号（例如 P004）。"}
            return {"state": "input_required",
                    "clarify": "请告诉我要报修哪台设备（资产编号，或设备名称关键词）。"}

        urgency = str(slots.get("urgency") or "medium").lower()
        attachments = slots.get("attachments")
        if isinstance(attachments, (list, dict)):
            attachments = json.dumps(attachments, ensure_ascii=False)

        args = {"fault_desc": fault, "asset_id": asset_id, "urgency": urgency,
                "reporter_id": req.employee_id}
        if attachments:
            args["attachments"] = attachments
        if slots.get("idempotency_key"):
            args["idempotency_key"] = slots["idempotency_key"]

        res = await call_mcp_tool("create_repair_ticket", args, server_url=settings.asset_mcp_url)
        if res.get("status") != "success":
            return {"state": "failed", "message": res.get("message", "报修失败")}

        ticket = (res.get("data") or {}).get("ticket", {}) or {}
        answer = (f"已创建报修工单 {ticket.get('ticket_no')}（设备 {asset_id}，"
                  f"紧急度 {URGENCY_LABEL.get(urgency, urgency)}）。IT 会按 SLA 跟进，"
                  f"你可以随时问「工单进度」或让我「催一下」。")
        logger.info("equipment_repair.created", ticket=ticket.get("ticket_no"),
                    asset=asset_id, urgency=urgency, reporter=req.employee_id)
        return {"state": "completed", "answer": answer, "data": {"ticket": ticket}}

    # ── 进度 ──

    async def _progress(self, req: TaskRequest) -> dict:
        if not req.employee_id:
            return {"state": "input_required", "clarify": "请先登录（缺少工号）。"}
        ticket_id = str(req.slots.get("ticket_id") or "").strip()

        if ticket_id:
            r = await call_mcp_tool("query_repair_ticket", {"ticket_id": ticket_id},
                                    server_url=settings.asset_mcp_url)
            if r.get("status") != "success":
                return {"state": "completed", "answer": f"没找到工单 {ticket_id}，请确认工单号。"}
            t = (r.get("data") or {}).get("ticket", {}) or {}
            return {"state": "completed",
                    "answer": f"工单 {t['ticket_no']} 当前{STATUS_LABEL.get(t.get('status'), t.get('status'))}，"
                              f"设备 {t.get('asset_id') or '—'}，SLA {t.get('sla_due_at') or '—'}。",
                    "data": {"ticket": t}}

        # 没给工单号：看我的工单（唯一一条直接报，多条列出来）
        r = await call_mcp_tool("list_my_tickets", {"reporter_id": req.employee_id},
                                server_url=settings.asset_mcp_url)
        tickets = ((r.get("data") or {}).get("tickets") or []) if r.get("status") == "success" else []
        if not tickets:
            return {"state": "completed", "answer": "你还没有报修工单。设备坏了可以直接说：投影仪 P004 画面偏色，帮我报修。"}
        if len(tickets) == 1:
            t = tickets[0]
            return {"state": "completed",
                    "answer": f"你有一个工单：{_ticket_line(t)}。",
                    "data": {"ticket": t}}
        lines = "；".join(_ticket_line(t) for t in tickets[:5])
        return {"state": "completed",
                "answer": f"你有 {len(tickets)} 个工单：{lines}。要看哪一个可以告诉我工单号。",
                "data": {"tickets": tickets}}

    async def _my_tickets(self, req: TaskRequest) -> dict:
        if not req.employee_id:
            return {"state": "input_required", "clarify": "请先登录（缺少工号）。"}
        r = await call_mcp_tool("list_my_tickets", {"reporter_id": req.employee_id},
                                server_url=settings.asset_mcp_url)
        tickets = ((r.get("data") or {}).get("tickets") or []) if r.get("status") == "success" else []
        if not tickets:
            return {"state": "completed", "answer": "你还没有报修工单。", "data": {"tickets": []}}
        lines = "；".join(_ticket_line(t) for t in tickets[:5])
        return {"state": "completed",
                "answer": f"你共有 {len(tickets)} 个工单：{lines}。",
                "data": {"tickets": tickets}}

    # ── 催单 ──

    async def _urge(self, req: TaskRequest) -> dict:
        if not req.employee_id:
            return {"state": "input_required", "clarify": "请先登录（缺少工号）。"}
        ticket_id = str(req.slots.get("ticket_id") or "").strip()

        if not ticket_id:      # 代查：唯一一条直接催，多条让用户选
            r = await call_mcp_tool("list_my_tickets", {"reporter_id": req.employee_id},
                                    server_url=settings.asset_mcp_url)
            tickets = ((r.get("data") or {}).get("tickets") or []) if r.get("status") == "success" else []
            open_tickets = [t for t in tickets if t.get("status") not in ("done", "closed")]
            if not open_tickets:
                return {"state": "completed", "answer": "你当前没有需要催的工单。"}
            if len(open_tickets) > 1:
                lines = "；".join(_ticket_line(t) for t in open_tickets[:5])
                return {"state": "input_required",
                        "clarify": f"你有多个处理中的工单，要催哪一个？{lines}",
                        "data": {"tickets": open_tickets}}
            ticket_id = open_tickets[0]["ticket_no"]

        res = await call_mcp_tool("urge_repair_ticket",
                                  {"ticket_id": ticket_id, "reporter_id": req.employee_id},
                                  server_url=settings.asset_mcp_url)
        if res.get("status") == "success":
            t = (res.get("data") or {}).get("ticket", {}) or {}
            if t.get("status") in ("done", "closed"):
                return {"state": "completed", "answer": f"工单 {ticket_id} 已完成，无需催单。",
                        "data": res.get("data")}
            return {"state": "completed",
                    "answer": f"已催单：{ticket_id}。IT 会尽快处理。",
                    "data": res.get("data")}
        if res.get("status") in ("rejected", "not_found"):
            return {"state": "completed",
                    "answer": str(res.get("message", "催单未完成")).rstrip("。") + "。"}
        return {"state": "failed", "message": res.get("message", "催单失败")}

    # ── A2A 同步入口 ──

    def handle_task(self, task: Task) -> Task:
        try:
            req = TaskRequest.parse(task)
            result = self.runtime.run(self._handle(req))
        except Exception as e:  # noqa: BLE001
            logger.error("equipment_repair.exception", error=str(e)[:300])
            return failed(task, message=f"报修服务异常：{e}")
        return render_result(task, result)


if __name__ == "__main__":
    configure_logging()
    agent = EquipmentRepairAgent()
    print(f"{AGENT_NAME} A2A → http://localhost:{PORT}")
    print(f"  skills: {[s.name for s in CARD.skills]}")
    run_server(agent, host="127.0.0.1", port=PORT)
