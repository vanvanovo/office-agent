# agents/meeting_book/server.py
# 会议室预订 Agent（A2A 独立服务 :5012）
#   能力：book（预订）/ reschedule（改期，先定新再放旧）/ cancel（取消，仅本人）
#   招牌链路：预订/改期前先【A2A 串行】调会议室查询 Agent 复查可用性，再落单——
#             查询结果只作参考，最终由 OA 侧事务原子校验兜底。
#
# 运行：python agents/meeting_book/server.py   → http://localhost:5012

from __future__ import annotations

import sys
from datetime import date, timedelta
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
from backend.config import get_settings  # noqa: E402
from backend.core.a2a_client import call_agent  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)
settings = get_settings()

AGENT_NAME = "MeetingBookAgent"
PORT = 5012

CARD = AgentCard(
    name=AGENT_NAME,
    description="会议室预订助手：预订 / 改期 / 取消。落单前先 A2A 调查询 Agent 复查可用性",
    url=f"http://localhost:{PORT}",
    version="1.0.0",
    capabilities={"streaming": False, "memory": False},
    skills=[
        AgentSkill(name="book_room", description="预订会议室（需先确认时段可用）",
                   examples=["帮我订明天下午 3 点 A-802"]),
        AgentSkill(name="reschedule_booking", description="会议室改期（先定新时段再释放原时段）",
                   examples=["把我下午的会改到 4 点"]),
        AgentSkill(name="cancel_booking", description="取消会议室预订（仅本人）",
                   examples=["取消 A-802 的预订"]),
        AgentSkill(name="recurring_booking", description="周期预订（每周固定时段订 N 周/订到某天）",
                   examples=["每周一 10 点帮我订 8 周 B-601"]),
    ],
)


def _alts_text(rooms: list[dict], limit: int = 3) -> str:
    if not rooms:
        return "暂无可用会议室，请换个时段试试"
    return "、".join(f"{r['room_id']}（{r['name']}，{r['capacity']}人）" for r in rooms[:limit])


class MeetingBookAgent(A2AServer):
    def __init__(self):
        super().__init__(agent_card=CARD)
        self.runtime = AgentRuntime()

    # ── 业务处理 ──

    async def _handle(self, req: TaskRequest) -> dict:
        action = (req.action or "book").lower()
        if action in ("book", "meeting_book"):
            return await self._book(req)
        if action in ("reschedule", "change"):
            return await self._reschedule(req)
        if action in ("recur", "recurring", "meeting_recur"):
            return await self._recur(req)
        if action == "cancel":
            return await self._cancel(req)
        return {"state": "input_required",
                "clarify": f"暂不支持的操作：{action}。支持：预订 / 改期 / 取消。"}

    async def _check_room_available(self, req: TaskRequest, room_id: str,
                                    date: str, start: str, end: str) -> tuple[bool, list[dict]]:
        """A2A 串行：调查询 Agent 复查目标时段是否可用。返回 (是否可用, 可用房间列表)。"""
        check = await call_agent(
            settings.meeting_query_agent_url, "query", req.query,
            context=req.context,
            slots={"date": date, "start_time": start, "end_time": end},
            employee=req.employee,
        )
        rooms: list[dict] = []
        if check.get("state") == "completed" and check.get("result"):
            rooms = (check["result"].get("data") or {}).get("rooms", []) or []
        available = any(r.get("room_id") == room_id for r in rooms)
        logger.info("meeting_book.serial_check", room=room_id, date=date,
                    slot=f"{start}-{end}", available=available, candidates=len(rooms))
        return available, rooms

    async def _book(self, req: TaskRequest) -> dict:
        slots = req.slots
        labels = {"room_id": "会议室编号", "date": "日期",
                  "start_time": "开始时间", "end_time": "结束时间"}
        missing = [v for k, v in labels.items() if not slots.get(k)]
        if not req.employee_id:
            missing.append("员工工号（登录信息）")
        if missing:
            return {"state": "input_required", "clarify": "请补充：" + "、".join(missing) + "。"}

        room_id, d, s, e = (slots["room_id"], slots["date"], slots["start_time"], slots["end_time"])

        # ① A2A 串行复查（查 → 订）
        available, rooms = await self._check_room_available(req, room_id, d, s, e)
        if not available:
            return {"state": "input_required",
                    "clarify": f"{room_id} 在 {d} {s}-{e} 不可用。可选：{_alts_text(rooms)}。要改订哪一间？",
                    "data": {"rooms": rooms}}

        # ② 落单（MCP 参数化工具；idempotency_key 由上游 Router 透传，重试必须复用）
        args = {"room_id": room_id, "date": d, "start_time": s, "end_time": e,
                "employee_id": req.employee_id}
        if slots.get("purpose"):
            args["purpose"] = slots["purpose"]
        if slots.get("idempotency_key"):
            args["idempotency_key"] = slots["idempotency_key"]

        res = await call_mcp_tool("book_room", args)
        if res.get("status") == "success":
            booking = (res.get("data") or {}).get("booking", {}) or {}
            answer = (f"预订成功：{booking.get('date')} {booking.get('start_time')}-{booking.get('end_time')} "
                      f"{booking.get('room_id')}，凭证 {booking.get('booking_id')}。")
            return {"state": "completed", "answer": answer, "data": res.get("data")}

        if res.get("status") == "conflict":
            # 竞争窗口（复查之后、落单之前被别人抢走）：复查备选时段
            _, rooms2 = await self._check_room_available(req, room_id, d, s, e)
            return {"state": "input_required",
                    "clarify": f"提交时该时段刚被抢占。可选：{_alts_text(rooms2)}。要改订哪一间？",
                    "data": {"rooms": rooms2}}

        return {"state": "failed", "message": res.get("message", "预订失败")}

    async def _reschedule(self, req: TaskRequest) -> dict:
        slots = req.slots
        labels = {"booking_id": "预订编号", "new_date": "新日期",
                  "new_start_time": "新开始时间", "new_end_time": "新结束时间"}
        missing = [v for k, v in labels.items() if not slots.get(k)]
        if not req.employee_id:
            missing.append("员工工号（登录信息）")
        if missing:
            return {"state": "input_required", "clarify": "请补充：" + "、".join(missing) + "。"}

        # ① 找到原预订（只能改自己的）
        res = await call_mcp_tool("list_my_bookings", {"employee_id": req.employee_id})
        bookings = (res.get("data") or {}).get("bookings", []) if res.get("status") == "success" else []
        old = next((b for b in bookings if b.get("booking_id") == slots["booking_id"]), None)
        if not old:
            return {"state": "completed",
                    "answer": f"没找到你可改期的预订 {slots['booking_id']}（只能改自己的预订）。"}

        nd, ns, ne = slots["new_date"], slots["new_start_time"], slots["new_end_time"]

        # ② A2A 串行：先确认目标时段原会议室仍可用（先定新）
        available, rooms = await self._check_room_available(req, old["room_id"], nd, ns, ne)
        if not available:
            return {"state": "input_required",
                    "clarify": f"{old['room_id']} 在 {nd} {ns}-{ne} 不可用。可选：{_alts_text(rooms)}；"
                               f"或保留原时段（{old['date']} {old['start_time']}-{old['end_time']}）。"}

        args = {"booking_id": slots["booking_id"], "new_date": nd, "new_start_time": ns,
                "new_end_time": ne, "employee_id": req.employee_id}
        if slots.get("idempotency_key"):
            args["idempotency_key"] = slots["idempotency_key"]

        # ③ 落单改期（成功后原时段自动释放）
        res = await call_mcp_tool("reschedule_booking", args)
        if res.get("status") == "success":
            booking = (res.get("data") or {}).get("booking", {}) or {}
            answer = (f"已改期：{booking.get('date')} {booking.get('start_time')}-{booking.get('end_time')} "
                      f"{booking.get('room_id')}，新凭证 {booking.get('booking_id')}；原时段已释放。")
            return {"state": "completed", "answer": answer, "data": res.get("data")}
        if res.get("status") == "conflict":
            _, rooms2 = await self._check_room_available(req, old["room_id"], nd, ns, ne)
            return {"state": "input_required",
                    "clarify": f"提交时目标时段刚被抢占。可选：{_alts_text(rooms2)}；或保留原时段。",
                    "data": {"rooms": rooms2}}
        if res.get("status") in ("rejected", "not_found"):
            return {"state": "completed", "answer": str(res.get("message", "改期未完成")).rstrip("。") + "。"}
        return {"state": "failed", "message": res.get("message", "改期失败")}

    async def _recur(self, req: TaskRequest) -> dict:
        """周期预订：每周固定时段，订 N 周（或订到某天）；先 A2A 复查首次时段。"""
        slots = req.slots
        if not req.employee_id:
            return {"state": "input_required", "clarify": "请先登录（缺少工号）。"}

        try:
            weekday = int(slots.get("weekday") or 0)
        except (TypeError, ValueError):
            weekday = 0
        if not (1 <= weekday <= 7):
            return {"state": "input_required",
                    "clarify": "请说明每周几，例如：每周一 10:00-11:00，订 8 周。"}
        s = str(slots.get("start_time") or "")
        e = str(slots.get("end_time") or "")
        if not s or not e:
            return {"state": "input_required", "clarify": "请说明时段，例如：每周一 10:00-11:00。"}
        room_id = str(slots.get("room_id") or "")
        if not room_id:
            return {"state": "input_required",
                    "clarify": "请说明要订哪一间会议室（可先让我查该时段的可用会议室）。"}

        first = date.today()
        while first.isoweekday() != weekday:
            first += timedelta(days=1)
        until = str(slots.get("until") or "")
        if not until:
            try:
                weeks = int(slots.get("weeks") or 0)
            except (TypeError, ValueError):
                weeks = 0
            if weeks < 1:
                return {"state": "input_required",
                        "clarify": "请说明订多少周（例如 8 周），或直接给截止日期。"}
            until = (first + timedelta(weeks=weeks - 1)).isoformat()

        # 周期预订不做"首次时段预检"：逐次落单、冲突场次逐条返回（单次预订才有查→订串联）

        args = {"room_id": room_id, "weekday": weekday, "start_time": s, "end_time": e,
                "until": until, "employee_id": req.employee_id}
        if slots.get("purpose"):
            args["purpose"] = slots["purpose"]
        if slots.get("idempotency_key"):
            args["idempotency_key"] = slots["idempotency_key"]

        res = await call_mcp_tool("book_recurring", args)
        if res.get("status") == "success":
            data = res.get("data") or {}
            conflicts = [r["date"] for r in (data.get("results") or []) if r.get("status") == "conflict"]
            weekday_name = "一二三四五六日"[weekday - 1]
            if data.get("booked", 0) == 0:
                answer = (f"很抱歉，{room_id} 每周{weekday_name} {s}-{e} 的 {data.get('total')} 次"
                          f"全部冲突，未创建预订：{'、'.join(conflicts[:6])}。"
                          f"建议换一间会议室或调整时段。")
            else:
                answer = (f"已创建周期预订：{room_id} 每周{weekday_name} {s}-{e}，"
                          f"共 {data.get('total')} 次（成功 {data.get('booked')} 次")
                if conflicts:
                    answer += f"，冲突 {len(conflicts)} 次：{'、'.join(conflicts[:6])}"
                answer += f"，截止 {data.get('until')}）。系列号 {data.get('series_id')}。"
            return {"state": "completed", "answer": answer, "data": {"series": data}}
        if res.get("status") in ("invalid", "not_found"):
            return {"state": "input_required",
                    "clarify": str(res.get("message", "周期参数不合法")).rstrip("。") + "。"}
        return {"state": "failed", "message": res.get("message", "周期预订失败")}

    async def _cancel(self, req: TaskRequest) -> dict:
        slots = req.slots
        if not slots.get("booking_id") or not req.employee_id:
            missing = []
            if not slots.get("booking_id"):
                missing.append("预订编号")
            if not req.employee_id:
                missing.append("员工工号（登录信息）")
            return {"state": "input_required", "clarify": "请补充：" + "、".join(missing) + "。"}

        res = await call_mcp_tool("cancel_booking",
                                  {"booking_id": slots["booking_id"],
                                   "employee_id": req.employee_id})
        if res.get("status") == "success":
            return {"state": "completed",
                    "answer": f"已取消预订 {slots['booking_id']}，时段已释放。",
                    "data": res.get("data")}
        if res.get("status") in ("rejected", "not_found"):
            return {"state": "completed",
                    "answer": str(res.get("message", "取消失败")).rstrip("。") + "。"}
        return {"state": "failed", "message": res.get("message", "取消失败")}

    # ── A2A 同步入口 ──

    def handle_task(self, task: Task) -> Task:
        try:
            req = TaskRequest.parse(task)
            result = self.runtime.run(self._handle(req))
        except Exception as e:  # noqa: BLE001
            logger.error("meeting_book.exception", error=str(e)[:300])
            return failed(task, message=f"会议室预订服务异常：{e}")
        return render_result(task, result)


if __name__ == "__main__":
    configure_logging()
    agent = MeetingBookAgent()
    print(f"{AGENT_NAME} A2A → http://localhost:{PORT}")
    print(f"  skills: {[s.name for s in CARD.skills]}")
    run_server(agent, host="127.0.0.1", port=PORT)
