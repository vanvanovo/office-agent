# scripts/check_channel.py
# P10 验收：Mock 钉钉渠道适配（双入口同链路）
#   1 你好（规则前置）→ 钉钉 markdown 回包
#   2 查会议室 → 回包含 A-801，且带 room_list 卡片
#   3 签名错误 → 401
#   4 未绑定工号 → 引导绑定
#   5 双入口一致性：同一问题，Web SSE 与钉钉回包都命中同一关键信息
#
# 前置：全部服务在跑（Mock/MCP/Agents/网关）
# 运行：python scripts/check_channel.py

import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from backend.config import get_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
BASE = "http://127.0.0.1:8010/api/v1"
WEBHOOK = f"{BASE}/channels/mock-dingtalk/webhook"
passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


def sign(staff_id: str, text: str) -> str:
    raw = f"{staff_id}:{text}:{settings.mcp_shared_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


async def dingtalk(c: httpx.AsyncClient, staff_id: str, text: str,
                   signature: str | None = None) -> httpx.Response:
    body = {"staff_id": staff_id, "text": text, "conversation_id": f"conv-{uuid.uuid4().hex[:6]}"}
    if signature:
        body["signature"] = signature
    return await c.post(WEBHOOK, json=body)


async def web_chat(token: str, text: str) -> str:
    tokens = ""
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream("POST", f"{BASE}/chat/stream",
                            json={"session_id": f"ch-{uuid.uuid4().hex[:8]}", "message": text},
                            headers={"Authorization": f"Bearer {token}"}) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                evt = json.loads(line[5:].strip())
                if evt.get("type") == "token":
                    tokens += evt.get("content", "")
    return tokens


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as c:
        print("[1] 问候（规则前置）")
        r = await dingtalk(c, "E1001", "你好", sign("E1001", "你好"))
        body = r.json()
        check("markdown 回包且含办公助手", r.status_code == 200 and body.get("msgtype") == "markdown"
              and "办公助手" in body.get("markdown", {}).get("text", ""), str(body)[:150])

        print("[2] 查会议室（携带卡片）")
        text2 = "明天下午3点A栋有哪些空会议室"
        r = await dingtalk(c, "E1001", text2, sign("E1001", text2))
        body = r.json()
        md = body.get("markdown", {}).get("text", "")
        cards = body.get("cards", [])
        check("回包含 A-801", "A-801" in md, md[:150])
        check("返回 room_list 卡片", any(x.get("type") == "room_list" for x in cards),
              str([x.get("type") for x in cards]))

        print("[3] 签名错误 → 401")
        r = await dingtalk(c, "E1001", "你好", "bad-sign")
        check("401", r.status_code == 401, f"status={r.status_code}")

        print("[4] 未绑定工号")
        r = await dingtalk(c, "X9999", "你好")
        check("引导绑定", "未绑定" in r.json().get("text", {}).get("content", ""), str(r.json())[:150])

        print("[5] 双入口一致性")
        t = (await c.post(f"{BASE}/auth/login", json={"emp_id": "E1001"})).json()["access_token"]
        web_reply = await web_chat(t, "报销流程是什么")
        r = await dingtalk(c, "E1001", "报销流程是什么", sign("E1001", "报销流程是什么"))
        dt_reply = r.json().get("markdown", {}).get("text", "")
        check("Web 与钉钉命中同一知识要点", ("发票" in web_reply or "报销单" in web_reply)
              and ("发票" in dt_reply or "报销单" in dt_reply),
              f"web={web_reply[:60]} dingtalk={dt_reply[:60]}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
