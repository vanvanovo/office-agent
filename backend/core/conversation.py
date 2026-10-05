# backend/core/conversation.py
# 对话内核（渠道无关）：一轮对话的完整处理，产出「内部事件流」。
#
# 渠道层只负责翻译事件：
#   - Web（SSE）：routing/progress/card/token/meta/done；
#   - IM（钉钉 mock）：把 reply 与 cards 组装成机器人回包。
# 双入口同链路——Web 与钉钉走的是同一个 run_turn_events()。

from __future__ import annotations

import json
import time
from typing import AsyncIterator

from backend.core import orchestrator
from backend.core import router as intent_router
from backend.core.logger import get_logger
from backend.core.session import (Session, add_clarify_round, append_history,
                                  reset_clarify)
from backend.db.engine import execute as db_execute

logger = get_logger(__name__)

GUIDE_TEXT = ("抱歉，连续两轮我都没能补齐信息。你可以直接说："
              "「查明天下午 A 栋的会议室」「订 A-801 明天 15:00-16:00」「我的预订」。"
              "也可以联系行政人工处理（本次诉求已记录待办池）。")


async def write_pool(session: Session, question: str, reason: str) -> None:
    """兜底池：超范围 / 超限 / 失败 / 人工 的诉求落库，有记录可跟进。"""
    try:
        await db_execute("app", """
            INSERT INTO unresolved_requests (emp_id, question, reason, context_summary)
            VALUES (:e, :q, :r, :c)
        """, {"e": session.emp_id, "q": question[:1000], "r": reason,
              "c": json.dumps(session.history[-4:], ensure_ascii=False)})
    except Exception as e:  # noqa: BLE001
        logger.warning("conversation.pool_write_failed", error=str(e)[:200])


def _close_turn(session: Session, user_text: str, assistant_text: str) -> None:
    append_history(session, "user", user_text)
    append_history(session, "assistant", assistant_text)


async def run_turn_events(session: Session, text: str) -> AsyncIterator[dict]:
    """一轮对话的事件流。

    事件类型：
      {"kind":"routing","intents":[...],"slots":{...},"need_clarify":bool}
      {"kind":"progress","stage":"...","label":"..."}
      {"kind":"card","card_type":"...","payload":{...}}
      {"kind":"reply","reply":"最终答复文本"}
      {"kind":"meta","elapsed_ms":int,"degraded":bool,"need_clarify":bool}
      {"kind":"error","message":"..."}
    """
    started = time.time()
    text = (text or "").strip()

    def elapsed() -> int:
        return int((time.time() - started) * 1000)

    try:
        # ① 规则前置（零 Token）
        pre = intent_router.prefilter(text)
        if pre and pre["kind"] == "reply":
            reset_clarify(session)
            _close_turn(session, text, pre["reply"])
            yield {"kind": "routing", "intents": ["chat"], "slots": {}, "need_clarify": False}
            yield {"kind": "reply", "reply": pre["reply"]}
            yield {"kind": "meta", "elapsed_ms": elapsed(), "degraded": False, "need_clarify": False}
            return
        if pre and pre["kind"] == "human":
            await write_pool(session, text, "human")
            _close_turn(session, text, pre["reply"])
            yield {"kind": "routing", "intents": ["human"], "slots": {}, "need_clarify": False}
            yield {"kind": "reply", "reply": pre["reply"]}
            yield {"kind": "meta", "elapsed_ms": elapsed(), "degraded": False, "need_clarify": False}
            return

        # ② LLM 意图识别
        yield {"kind": "progress", "stage": "intent", "label": "正在理解你的问题…"}
        parsed = await intent_router.classify(text, session)
        intents = parsed["intents"]
        slots = parsed["slots"]
        biz = [i for i in intents if i in intent_router.BUSINESS_INTENTS]

        # ③ 非业务意图（闲聊 / 越界 / 人工 / 解析失败）
        if not biz:
            if "chat" in intents and parsed.get("chat_reply"):
                reply = parsed["chat_reply"]
            elif "human" in intents:
                await write_pool(session, text, "human")
                reply = "好的，我已记录你的诉求，会转交行政/IT 人工跟进。"
            elif parsed.get("parse_failed"):
                await write_pool(session, text, "agent_failed")
                reply = parsed.get("clarify") or "抱歉，我没太理解，能换个说法再试一次吗？"
            else:
                await write_pool(session, text, "out_of_scope")
                reply = ("这个问题暂时不在我的能力范围内。目前支持：查/订会议室、器材查询与报修、"
                         "制度问答、改期/取消等。已把你的问题记入待办池，可联系行政协助。")
            reset_clarify(session)
            _close_turn(session, text, reply)
            yield {"kind": "routing", "intents": intents, "slots": slots,
                   "need_clarify": bool(parsed.get("parse_failed"))}
            yield {"kind": "reply", "reply": reply}
            yield {"kind": "meta", "elapsed_ms": elapsed(), "degraded": False,
                   "need_clarify": bool(parsed.get("parse_failed"))}
            return

        # ④ 槽位校验（服务端权威）→ 追问（≤2 轮）
        missing: list[str] = []
        for i in biz:
            for k in intent_router.missing_required(i, slots):
                if k not in missing:
                    missing.append(k)

        need_clarify = bool(parsed["need_clarify"] or missing)
        yield {"kind": "routing", "intents": intents, "slots": slots,
               "need_clarify": need_clarify}

        if need_clarify:
            clarify = intent_router.build_clarify(biz[0], missing) if missing \
                else (parsed.get("clarify") or "请补充相关信息。")
            rounds = add_clarify_round(session)
            if rounds > 2:                                # 第 3 次：引导 + 兜底池
                await write_pool(session, text, "clarify_limit")
                reset_clarify(session)
                clarify = GUIDE_TEXT
            _close_turn(session, text, clarify)
            yield {"kind": "reply", "reply": clarify}
            yield {"kind": "meta", "elapsed_ms": elapsed(), "degraded": False,
                   "need_clarify": True}
            return

        # ⑤ 编排执行（A2A 分发）
        reset_clarify(session)
        yield {"kind": "progress", "stage": "dispatch", "label": "正在处理你的请求…"}
        result = await orchestrator.execute(biz, parsed["user_queries"], slots, session)

        for c in result["cards"]:
            yield {"kind": "card", "card_type": c["type"], "payload": c["payload"]}

        reply = result["reply"] or "已完成。"
        if result["need_clarify"]:                        # 业务侧追问也计入轮次
            rounds = add_clarify_round(session)
            if rounds > 2:
                await write_pool(session, text, "clarify_limit")
                reset_clarify(session)
                reply = GUIDE_TEXT
        _close_turn(session, text, reply)
        yield {"kind": "reply", "reply": reply}
        yield {"kind": "meta", "elapsed_ms": elapsed(), "degraded": result["degraded"],
               "need_clarify": result["need_clarify"]}

    except Exception as e:  # noqa: BLE001 —— 内核永不抛出，异常作为事件返回
        logger.error("conversation.turn_failed", error=str(e)[:300], exc_info=True)
        yield {"kind": "error", "message": f"服务异常：{e}"}
        yield {"kind": "meta", "elapsed_ms": elapsed(), "degraded": True, "need_clarify": False}
