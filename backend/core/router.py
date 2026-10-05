# backend/core/router.py
# 意图 Router：规则前置（零 Token）→ LLM 意图 JSON → 服务端槽位校验 → 追问文案。
#
# 设计说明：办公助手用「大模型 + 提示词」路由（要判断条件完整性、还要生成追问文案）。

import json
import re
from typing import Optional

from backend.core.llm_factory import ainvoke_with_fallback
from backend.core.logger import get_logger
from backend.core.prompts import INTENT_PROMPT_VERSION, build_intent_messages
from backend.core.session import Session

logger = get_logger(__name__)

BUSINESS_INTENTS = {"meeting_query", "meeting_book", "meeting_reschedule",
                    "meeting_cancel", "meeting_my", "meeting_recur",
                    "equipment_query", "equipment_repair", "equipment_my", "equipment_urge",
                    "kb_qa"}
ALL_INTENTS = BUSINESS_INTENTS | {"chat", "human", "out_of_scope"}

# 服务端权威的必要槽位（room_id / booking_id / asset_id / ticket_id 可由系统代查，不算硬缺口）
REQUIRED_SLOTS = {
    "meeting_query": ["date", "start_time", "end_time"],
    "meeting_book": ["date", "start_time", "end_time"],
    "meeting_reschedule": ["new_date", "new_start_time", "new_end_time"],
    "meeting_cancel": [],
    "meeting_my": [],
    "meeting_recur": [],
    "equipment_query": [],
    "equipment_repair": ["fault_desc"],
    "equipment_my": [],
    "equipment_urge": [],
    "kb_qa": [],
}

SLOT_LABELS = {
    "date": "日期", "start_time": "开始时间", "end_time": "结束时间",
    "room_id": "会议室编号", "building": "楼栋",
    "new_date": "新日期", "new_start_time": "新开始时间", "new_end_time": "新结束时间",
    "category": "器材类别", "keyword": "器材关键词", "department": "部门",
    "asset_id": "资产编号", "fault_desc": "故障描述", "urgency": "紧急度", "ticket_id": "工单号",
}

GREETING_RE = re.compile(
    r"^(你好|您好|hi|hello|嗨|在吗|早上好|下午好|晚上好|你是谁|你能做什么|你能帮我做什么|help|帮助)"
    r"[!！。.？?~～\s]*$", re.I)
THANKS_RE = re.compile(r"^(谢谢|多谢|感谢|thanks|thank\s*you)[!！。.~～\s]*$", re.I)
BYE_RE = re.compile(r"^(再见|拜拜|bye|goodbye)[!！。.~～\s]*$", re.I)
HUMAN_RE = re.compile(r"(转人工|人工客服|找人工|联系行政|找行政|找\s*IT|叫行政)", re.I)

RULE_REPLIES = {
    "chat": "你好，我是办公助手。可以帮你：查/订会议室、改期或取消预订、查看我的预订。"
            "试试说「明天下午3点A栋有哪些空会议室」。",
    "thanks": "不客气～ 还有其他需要随时说。",
    "bye": "再见，祝你工作顺利！",
    "empty": "请告诉我你的需求，例如：明天下午 3 点 A 栋有哪些空会议室。",
}


def prefilter(text: str) -> Optional[dict]:
    """规则前置：命中则零 Token 直答。返回 {"kind": "reply"|"human", "reply": ...}。"""
    t = (text or "").strip()
    if not t:
        return {"kind": "reply", "reply": RULE_REPLIES["empty"]}
    if GREETING_RE.match(t):
        return {"kind": "reply", "reply": RULE_REPLIES["chat"]}
    if THANKS_RE.match(t):
        return {"kind": "reply", "reply": RULE_REPLIES["thanks"]}
    if BYE_RE.match(t):
        return {"kind": "reply", "reply": RULE_REPLIES["bye"]}
    if HUMAN_RE.search(t):
        return {"kind": "human", "reply": "好的，我已记录你的诉求，会转交行政/IT 人工跟进。"}
    return None


def _parse_json(raw: str) -> Optional[dict]:
    """解析 LLM 输出：先剥 Markdown 代码块，再兜底抓第一个 JSON 对象。"""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _fallback_result(clarify: str) -> dict:
    return {"intents": [], "user_queries": {}, "slots": {}, "need_clarify": True,
            "clarify": clarify, "confidence": 0.0, "chat_reply": "",
            "parse_failed": True, "prompt_version": INTENT_PROMPT_VERSION}


async def classify(text: str, session: Session) -> dict:
    """LLM 意图识别：一次调用输出 intents / user_queries / slots / need_clarify / chat_reply。

    解析失败时自动纠正重试一次（把失败输出作为上下文，要求只输出 JSON）。
    """
    from langchain_core.messages import SystemMessage

    last_raw = ""
    data: Optional[dict] = None

    for attempt in (1, 2):
        messages = build_intent_messages(session.history, text)
        if attempt == 2:
            messages.append(SystemMessage(
                content=f"你上一次的输出不是合法 JSON（原样如下）：{last_raw[:200]}\n"
                        f"请只输出规定格式的 JSON，不要回答业务内容。"))
        try:
            resp = await ainvoke_with_fallback("intent", messages)
            last_raw = str(getattr(resp, "text", None) or getattr(resp, "content", ""))
        except Exception as e:  # noqa: BLE001
            logger.error("router.llm_failed", error=str(e)[:250], attempt=attempt)
            return _fallback_result("抱歉，我暂时无法处理，请稍后再试。")

        data = _parse_json(last_raw)
        if isinstance(data, dict):
            break
        logger.warning("router.parse_failed", raw=last_raw[:200], attempt=attempt)

    if not isinstance(data, dict):
        return _fallback_result("抱歉，我没太理解，能换个说法再试一次吗？")

    intents = [str(i).strip() for i in (data.get("intents") or []) if str(i).strip()]
    intents = [i for i in intents if i in ALL_INTENTS]
    if not intents:
        intents = ["out_of_scope"]

    slots = data.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    slots = {k: v for k, v in slots.items() if v not in (None, "", "null", "NULL")}

    uq = data.get("user_queries") or {}
    if not isinstance(uq, dict):
        uq = {}

    result = {
        "intents": intents,
        "user_queries": {str(k): str(v) for k, v in uq.items()},
        "slots": slots,
        "need_clarify": bool(data.get("need_clarify")),
        "clarify": str(data.get("clarify") or "").strip(),
        "confidence": float(data.get("confidence") or 0),
        "chat_reply": str(data.get("chat_reply") or "").strip(),
        "parse_failed": False,
        "prompt_version": INTENT_PROMPT_VERSION,
    }
    logger.info("router.classified", intents=result["intents"], need_clarify=result["need_clarify"],
                confidence=result["confidence"], slots=result["slots"])
    return result


def missing_required(intent: str, slots: dict) -> list[str]:
    return [k for k in REQUIRED_SLOTS.get(intent, []) if not slots.get(k)]


def build_clarify(intent: str, missing: list[str]) -> str:
    labels = "、".join(SLOT_LABELS.get(k, k) for k in missing)
    examples = {
        "meeting_query": "，例如：明天 15:00-16:00 A 栋",
        "meeting_book": "，例如：明天 15:00-16:00 订 A-802",
        "meeting_reschedule": "，例如：改成明天 16:00-17:00",
        "equipment_repair": "，例如：P004 画面偏色，挺急的",
    }.get(intent, "")
    return f"请补充：{labels}{examples}。"
