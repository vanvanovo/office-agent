# scripts/check_chat.py
# P4 验收：统一对话入口（SSE 全链路）——V1 会议域
#   1 问候（规则前置，零 Token）
#   2 查询会议室（LLM 意图 → A2A → 卡片）
#   3 预订缺参 → 追问
#   4 补充信息 → 预订成功（多轮上下文）
#   5 我的预订
#   6 越权取消（另一身份）→ 拒绝
#   7 本人取消 → 成功
#   8 越界问题 → 引导 + 兜底池
#   9 连续追问上限 → 引导话术
#  10 未登录 → 401；兜底池落库断言
#
# 前置：Mock:8210 / OA MCP:8111 / 查询 Agent:5011 / 预订 Agent:5012 / 网关:8010 已启动
# 运行：python scripts/check_chat.py

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
BASE = "http://127.0.0.1:8010/api/v1"

passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


async def login(emp_id: str) -> str:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{BASE}/auth/login", json={"emp_id": emp_id})
        r.raise_for_status()
        return r.json()["access_token"]


async def chat(session_id: str, message: str, token: str) -> dict:
    out = {"tokens": "", "cards": [], "routings": [], "metas": [], "errors": []}
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream(
            "POST", f"{BASE}/chat/stream",
            json={"session_id": session_id, "message": message},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                evt = json.loads(raw)
                t = evt.get("type")
                if t == "token":
                    out["tokens"] += evt.get("content", "")
                elif t == "card":
                    out["cards"].append(evt)
                elif t == "routing":
                    out["routings"].append(evt)
                elif t == "meta":
                    out["metas"].append(evt)
                elif t == "error":
                    out["errors"].append(evt)
    return out


def first_booking_id(events: dict) -> str | None:
    for c in events["cards"]:
        if c.get("card_type") == "booking":
            return (c.get("payload") or {}).get("booking_id")
    return None


async def main() -> int:
    print("[0] 登录（E1001 / E1002）")
    t1 = await login("E1001")
    t2 = await login("E1002")
    check("两个身份均签发 JWT", bool(t1) and bool(t2))

    sid = f"check-{uuid.uuid4().hex[:8]}"

    print("[1] 问候（规则前置）")
    r = await chat(sid, "你好", t1)
    check("回复为助手介绍", "办公助手" in r["tokens"], r["tokens"][:100])
    check("无错误事件", not r["errors"], str(r["errors"]))

    print("[2] 查询会议室")
    r = await chat(sid, "明天下午3点A栋有哪些空会议室", t1)
    intents = r["routings"][-1]["intents"] if r["routings"] else []
    check("意图 meeting_query", "meeting_query" in intents, str(intents))
    check("回答包含 A-801", "A-801" in r["tokens"], r["tokens"][:200])
    check("返回 room_list 卡片",
          any(c.get("card_type") == "room_list" for c in r["cards"]),
          f"cards={[c.get('card_type') for c in r['cards']]}")

    print("[3] 预订缺参 → 追问")
    r = await chat(sid, "帮我订个10人的会议室", t1)
    check("返回追问（缺时间/房间）",
          ("补充" in r["tokens"]) or ("可用" in r["tokens"]) or ("哪一间" in r["tokens"]),
          r["tokens"][:200])

    print("[4] 补充信息 → 预订成功（多轮上下文合并）")
    r = await chat(sid, "A-802，明天下午3点到4点", t1)
    bid = first_booking_id(r)
    check("预订成功且带凭证", ("预订成功" in r["tokens"]) and bool(bid),
          f"tokens={r['tokens'][:200]} bid={bid}")
    if bid:
        print(f"      booking_id = {bid}")

    print("[5] 我的预订")
    r = await chat(sid, "我的预订", t1)
    check("我的预订包含刚订的单", bool(bid) and (bid in r["tokens"] or any(
        c.get("card_type") == "booking_list" for c in r["cards"])), r["tokens"][:200])

    print("[6] 越权取消（E1002 取消 E1001 的预订）→ 拒绝")
    if bid:
        sid2 = f"check-{uuid.uuid4().hex[:8]}"
        r = await chat(sid2, f"取消预订 {bid}", t2)
        check("越权取消被拒绝", "只能" in r["tokens"], r["tokens"][:200])
    else:
        check("越权取消被拒绝", False, "缺少 booking_id")

    print("[7] 本人取消 → 成功")
    if bid:
        r = await chat(sid, f"取消预订 {bid}", t1)
        check("取消成功", "已取消" in r["tokens"], r["tokens"][:200])
    else:
        check("取消成功", False, "缺少 booking_id")

    print("[8] 越界问题 → 引导")
    r = await chat(sid, "今天天气怎么样", t1)
    check("越界返回能力范围引导", "能力范围" in r["tokens"] or "待办池" in r["tokens"],
          r["tokens"][:200])

    print("[9] 连续追问上限 → 引导话术")
    sid3 = f"check-{uuid.uuid4().hex[:8]}"
    for i in range(3):
        r = await chat(sid3, "帮我订会议室", t1)
    check("第 3 次触发引导（不再追问）", "连续两轮" in r["tokens"], r["tokens"][:200])

    print("[10] 鉴权与兜底池")
    async with httpx.AsyncClient(timeout=10) as c:
        r401 = await c.post(f"{BASE}/chat/stream",
                            json={"session_id": "x", "message": "你好"})
    check("未登录 → 401", r401.status_code == 401, f"status={r401.status_code}")

    engine = create_async_engine(settings.mysql_app_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT reason, COUNT(*) AS c FROM unresolved_requests GROUP BY reason"
            ))).mappings().all()
    finally:
        await engine.dispose()
    pool = {r["reason"]: r["c"] for r in rows}
    check("兜底池：越界已落库", pool.get("out_of_scope", 0) >= 1, str(pool))
    check("兜底池：追问超限已落库", pool.get("clarify_limit", 0) >= 1, str(pool))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
