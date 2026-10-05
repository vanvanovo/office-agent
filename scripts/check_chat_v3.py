# scripts/check_chat_v3.py
# P6 验收（2/2）：SSE 全链路 —— 知识问答（A2A 外接 RAG）
#   1 报销流程（意图 kb_qa + 回答 + 来源卡片）
#   2 器材借用期限
#   3 多意图（知识 + 我的预订）
#   4 知识库没有的问题 → 拒答
#
# 前置：全部服务在跑（含 Milvus 19532 / RAG Agent 5015）
# 运行：python scripts/check_chat_v3.py

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
    out = {"tokens": "", "cards": [], "routings": [], "metas": [], "errors": []}
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
                elif t == "meta":
                    out["metas"].append(evt)
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
    sid = f"v3-{uuid.uuid4().hex[:8]}"

    print("[1] 报销流程")
    r = await chat(sid, "报销流程是什么", token)
    intents = r["routings"][-1]["intents"] if r["routings"] else []
    check("意图 kb_qa", "kb_qa" in intents, str(intents))
    check("回答含流程要点", ("发票" in r["tokens"] or "报销单" in r["tokens"]) and
          ("财务" in r["tokens"] or "打款" in r["tokens"]), r["tokens"][:200])
    card = card_of(r, "rag_sources")
    check("来源卡片 ≥1 条", card is not None and len(card.get("payload", {}).get("sources", [])) >= 1,
          f"cards={[c.get('card_type') for c in r['cards']]}")

    print("[2] 器材借用期限")
    r = await chat(sid, "器材借用期限是多久", token)
    check("回答含 7 天/30 天", "7 天" in r["tokens"] or "30 天" in r["tokens"], r["tokens"][:200])

    print("[3] 多意图（知识 + 我的预订）")
    r = await chat(sid, "报销流程是什么，另外我明天有哪些会", token)
    intents = r["routings"][-1]["intents"] if r["routings"] else []
    check("意图含 kb_qa 与 meeting_my", "kb_qa" in intents and "meeting_my" in intents, str(intents))

    print("[4] 库外制度问题 → RAG 拒答")
    r = await chat(sid, "公司健身房使用规定是什么", token)
    check("拒答不编造", "没有找到" in r["tokens"], r["tokens"][:200])

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
