# scripts/check_chat_v2.py
# P5 验收（3/3）：SSE 全链路 —— 器材域
#   1 器材查询（类别+部门，静态+动态融合）
#   2 报修（指定资产，出单 + 卡片）
#   3 工单进度
#   4 催单
#   5 我的工单
#   6 多意图（器材 + 我的预订）
#   7 报修设备模糊 → 候选追问
#
# 前置：全部服务在跑（8210/8111/8112/5011/5012/5013/5014/8010）
# 运行：python scripts/check_chat_v2.py

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

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
    out = {"tokens": "", "cards": [], "routings": [], "errors": []}
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream("POST", f"{BASE}/chat/stream",
                            json={"session_id": session_id, "message": message},
                            headers={"Authorization": f"Bearer {token}"}) as resp:
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
                elif t == "error":
                    out["errors"].append(evt)
    return out


def card_of(events: dict, card_type: str) -> dict | None:
    for c in events["cards"]:
        if c.get("card_type") == card_type:
            return c
    return None


async def main() -> int:
    token = await login("E1001")
    sid = f"v2-{uuid.uuid4().hex[:8]}"

    print("[1] 器材查询（设计部投影仪）")
    r = await chat(sid, "设计部还有几台投影仪可以借", token)
    intents = r["routings"][-1]["intents"] if r["routings"] else []
    check("意图 equipment_query", "equipment_query" in intents, str(intents))
    check("回答含 P001", "P001" in r["tokens"], r["tokens"][:200])
    check("equipment_list 卡片", card_of(r, "equipment_list") is not None,
          f"cards={[c.get('card_type') for c in r['cards']]}")

    print("[2] 报修（P004，急）")
    r = await chat(sid, "P004 投影仪屏幕碎了，帮我报修，急", token)
    card = card_of(r, "repair_ticket")
    tno = (card or {}).get("payload", {}).get("ticket_no")
    check("报修成功且有工单号", "已创建报修工单" in r["tokens"] and bool(tno),
          f"tokens={r['tokens'][:200]} tno={tno}")
    if tno:
        print(f"      ticket_no = {tno}")

    print("[3] 工单进度")
    if tno:
        r = await chat(sid, f"查一下工单 {tno} 的进度", token)
        check("进度返回待处理", "待处理" in r["tokens"], r["tokens"][:200])
    else:
        check("进度返回待处理", False, "缺少工单号")

    print("[4] 催单")
    if tno:
        r = await chat(sid, f"帮我催一下 {tno}", token)
        check("催单成功", "已催单" in r["tokens"], r["tokens"][:200])
    else:
        check("催单成功", False, "缺少工单号")

    print("[5] 我的工单")
    r = await chat(sid, "我的工单", token)
    check("ticket_list 卡片", card_of(r, "ticket_list") is not None,
          f"cards={[c.get('card_type') for c in r['cards']]}")

    print("[6] 多意图（器材 + 我的预订）")
    r = await chat(sid, "查下投影仪，再帮我看看我的预订", token)
    intents = r["routings"][-1]["intents"] if r["routings"] else []
    check("意图含器材与会议两个",
          "equipment_query" in intents and "meeting_my" in intents, str(intents))

    print("[7] 报修设备模糊 → 候选追问")
    r = await chat(sid, "笔记本屏幕碎了帮我报修", token)
    check("返回候选追问", "多台" in r["tokens"] or "候选" in r["tokens"], r["tokens"][:200])

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
