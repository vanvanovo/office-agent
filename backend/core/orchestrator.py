# backend/core/orchestrator.py
# 编排层：把 Router 识别出的意图分发到对应 A2A Agent，回收结果、组装卡片与最终答复。
#
# 特色分支：
#   - 预订缺 room_id：先调查询 Agent 给出可用会议室选项，再追问"订哪一间"；
#   - 改期/取消缺 booking_id：用「我的预订」代查——唯一一条自动采用，多条列出让用户选。

from backend.config import get_settings
from backend.core.a2a_client import call_agent
from backend.core.exceptions import A2ACallError
from backend.core.logger import get_logger
from backend.core.session import Session

logger = get_logger(__name__)
settings = get_settings()

# 意图 →（Agent 地址配置项，action）
INTENT_ROUTES = {
    "meeting_query":      ("meeting_query_agent_url", "query"),
    "meeting_my":         ("meeting_query_agent_url", "my_bookings"),
    "meeting_book":       ("meeting_book_agent_url", "book"),
    "meeting_reschedule": ("meeting_book_agent_url", "reschedule"),
    "meeting_cancel":     ("meeting_book_agent_url", "cancel"),
    "meeting_recur":      ("meeting_book_agent_url", "recur"),
    "equipment_query":    ("equipment_query_agent_url", "query"),
    "equipment_repair":   ("equipment_repair_agent_url", "repair"),
    "equipment_my":       ("equipment_repair_agent_url", "my_tickets"),
    "equipment_urge":     ("equipment_repair_agent_url", "urge"),
    "kb_qa":              ("rag_agent_url", "ask"),
}


async def _call(intent: str, query: str, slots: dict, session: Session) -> dict:
    cfg_attr, action = INTENT_ROUTES[intent]
    url = getattr(settings, cfg_attr)
    return await call_agent(url, action, query, context=session.history[-6:],
                            slots=slots, employee={"id": session.emp_id, "role": session.role})


def _cards_from_data(data: dict, cards: list[dict]) -> None:
    if data.get("rooms"):
        cards.append({"type": "room_list", "payload": {"rooms": data["rooms"]}})
    if data.get("booking"):
        cards.append({"type": "booking", "payload": data["booking"]})
    if data.get("bookings"):
        cards.append({"type": "booking_list", "payload": {"bookings": data["bookings"]}})
    if data.get("equipment"):
        cards.append({"type": "equipment_list", "payload": {"equipment": data["equipment"]}})
    if data.get("ticket"):
        cards.append({"type": "repair_ticket", "payload": data["ticket"]})
    if data.get("tickets"):
        cards.append({"type": "ticket_list", "payload": {"tickets": data["tickets"]}})
    if data.get("sources"):
        cards.append({"type": "rag_sources", "payload": {"sources": data["sources"]}})
    if data.get("series"):
        cards.append({"type": "recurring_result", "payload": data["series"]})


def _alts_text(rooms: list[dict], limit: int = 5) -> str:
    return "、".join(f"{r['room_id']}（{r['name']}，{r['capacity']}人）" for r in rooms[:limit])


async def _resolve_booking_id(slots: dict, session: Session) -> tuple[bool, str, list[dict]]:
    """改期/取消缺 booking_id：用我的预订代查。返回 (是否已解析, 追问文案, 预订列表)。"""
    r = await _call("meeting_my", "查询我的预订", {}, session)
    bookings = ((r.get("result") or {}).get("data") or {}).get("bookings", []) \
        if r.get("state") == "completed" else []
    if not bookings:
        return False, "你当前没有可操作的预订。", []
    if len(bookings) == 1:
        slots["booking_id"] = bookings[0]["booking_id"]
        return True, "", bookings
    lines = [f"{b['booking_id']}（{b['date']} {b['start_time']}-{b['end_time']} {b['room_id']}）"
             for b in bookings]
    return False, "你有多个预订，请告诉我要操作哪一个：" + "；".join(lines), bookings


async def _resolve_ticket_id(slots: dict, session: Session) -> tuple[bool, str, list[dict]]:
    """催单缺 ticket_id：用我的工单代查（只看处理中的）。返回 (是否已解析, 追问文案, 工单列表)。"""
    r = await _call("equipment_my", "我的工单", {}, session)
    tickets = ((r.get("result") or {}).get("data") or {}).get("tickets", []) \
        if r.get("state") == "completed" else []
    open_tickets = [t for t in tickets if t.get("status") not in ("done", "closed")]
    if not open_tickets:
        return False, "你当前没有需要催的工单。", []
    if len(open_tickets) == 1:
        slots["ticket_id"] = open_tickets[0]["ticket_no"]
        return True, "", open_tickets
    lines = [f"{t['ticket_no']}（{t.get('asset_id') or '—'}，{t.get('fault_desc') or ''}）"
             for t in open_tickets]
    return False, "你有多个处理中的工单，要催哪一个？" + "；".join(lines), open_tickets


async def execute(intents: list[str], user_queries: dict, slots: dict, session: Session) -> dict:
    """逐个执行业务意图，返回 {reply, cards, need_clarify, degraded}。"""
    reply_parts: list[str] = []
    cards: list[dict] = []
    degraded = False
    need_clarify = False
    clarify_text = ""

    for intent in intents:
        # 改期/取消：缺 booking_id 先代查
        if intent in ("meeting_reschedule", "meeting_cancel") and not slots.get("booking_id"):
            ok, msg, bookings = await _resolve_booking_id(slots, session)
            if not ok:
                need_clarify, clarify_text = True, msg
                if bookings:
                    cards.append({"type": "booking_list", "payload": {"bookings": bookings}})
                break

        # 催单：缺 ticket_id 先代查
        if intent == "equipment_urge" and not slots.get("ticket_id"):
            ok, msg, tickets = await _resolve_ticket_id(slots, session)
            if not ok:
                need_clarify, clarify_text = True, msg
                if tickets:
                    cards.append({"type": "ticket_list", "payload": {"tickets": tickets}})
                break

        # 预订缺 room_id：先查可用给选项，再追问
        if intent == "meeting_book" and not slots.get("room_id"):
            q = await _call("meeting_query", user_queries.get(intent, "查询可用会议室"), slots, session)
            rooms = ((q.get("result") or {}).get("data") or {}).get("rooms", []) \
                if q.get("state") == "completed" else []
            need_clarify = True
            if rooms:
                clarify_text = f"该时段可用：{_alts_text(rooms)}。要订哪一间？"
                cards.append({"type": "room_list", "payload": {"rooms": rooms}})
            else:
                clarify_text = "该时段没有可用会议室，可以换个时间或让我再查一次。"
            break

        # 正常调用 Agent
        try:
            r = await _call(intent, user_queries.get(intent, ""), slots, session)
        except A2ACallError as e:
            logger.error("orchestrator.a2a_failed", intent=intent, error=str(e)[:200])
            degraded = True
            reply_parts.append("部分服务暂时不可用，请稍后再试。")
            continue

        if r.get("state") == "completed":
            res = r.get("result") or {}
            if res.get("answer"):
                reply_parts.append(res["answer"])
            _cards_from_data(res.get("data") or {}, cards)
        elif r.get("state") == "input_required":
            need_clarify = True
            clarify_text = r.get("message") or "请补充相关信息。"
            _cards_from_data((r.get("result") or {}).get("data") or {}, cards)
        else:
            degraded = True
            reply_parts.append(r.get("message") or "处理失败，请稍后再试。")

    if need_clarify:
        reply = clarify_text
    else:
        reply = " ".join(reply_parts).strip()
    return {"reply": reply, "cards": cards, "need_clarify": need_clarify, "degraded": degraded}
