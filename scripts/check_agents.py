# scripts/check_agents.py
# P3 验收：会议室查询 / 预订两个 A2A Agent（直连调用）
#   1 查询可用（实时数据）
#   2 查询缺参 → input-required
#   3 预订成功（内部先 A2A 串行复查可用性，再落单）
#   4 预订不可用房间 → input-required + 备选建议
#   5 我的预订
#   6 改期（先定新再放旧）
#   7 改期后列表：新单在、旧单不在
#   8 越权取消 → 业务拒绝
#   9 本人取消 → 成功
#  10 预订缺房间 → input-required
#
# 前置：Mock(:8210) / OA MCP(:8111) / 查询 Agent(:5011) / 预订 Agent(:5012) 已启动
# 运行：python scripts/check_agents.py

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.core.a2a_client import call_agent  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
Q = settings.meeting_query_agent_url      # :5011
B = settings.meeting_book_agent_url       # :5012
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
EMP1 = {"id": "E1001", "role": "employee"}
EMP2 = {"id": "E1002", "role": "employee"}

passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


def _data(r: dict) -> dict:
    return (r.get("result") or {}).get("data") or {}


async def main() -> int:
    print("[1] 查询可用会议室（明天 15:00-16:00 A 栋）")
    r = await call_agent(Q, "query", "查明天15:00-16:00 A栋", employee=EMP1,
                         slots={"date": TOMORROW, "start_time": "15:00",
                                "end_time": "16:00", "building": "A"})
    ids = [x["room_id"] for x in _data(r).get("rooms", [])]
    check("查询完成：A-801 可用、被占用的 A-501 不在",
          r["state"] == "completed" and "A-801" in ids and "A-501" not in ids,
          f"state={r['state']} ids={ids}")
    check("回答含房间信息", "A-801" in ((r.get("result") or {}).get("answer", "")),
          str(r.get("result"))[:200])

    print("[2] 查询缺参 → input-required")
    r = await call_agent(Q, "query", "查会议室", employee=EMP1, slots={"date": TOMORROW})
    check("缺时间返回追问", r["state"] == "input_required"
          and ("开始时间" in r.get("message", "") or "结束时间" in r.get("message", "")),
          f"state={r['state']} msg={r.get('message')}")

    print("[3] 预订成功（内部 A2A 串行复查 → 落单）")
    idem = f"agent-check-{uuid.uuid4().hex[:8]}"
    r = await call_agent(B, "book", "订 A-802 明天 15-16 点", employee=EMP1,
                         slots={"room_id": "A-802", "date": TOMORROW, "start_time": "15:00",
                                "end_time": "16:00", "purpose": "验收", "idempotency_key": idem})
    booking = _data(r).get("booking") or {}
    bid = booking.get("booking_id")
    check("预订完成且返回凭证", r["state"] == "completed" and bool(bid),
          f"state={r['state']} result={str(r.get('result'))[:200]}")

    print("[4] 预订不可用房间 → input-required + 备选")
    r = await call_agent(B, "book", "订 A-501", employee=EMP1,
                         slots={"room_id": "A-501", "date": TOMORROW, "start_time": "15:00",
                                "end_time": "16:00"})
    check("不可用房间返回追问与备选", r["state"] == "input_required"
          and "不可用" in r.get("message", "") and "可选" in r.get("message", ""),
          f"state={r['state']} msg={r.get('message')}")

    print("[5] 我的预订")
    r = await call_agent(Q, "my_bookings", "我的预订", employee=EMP1)
    my_ids = [b["booking_id"] for b in _data(r).get("bookings", [])]
    check("我的预订包含新单", r["state"] == "completed" and bid in my_ids, f"ids={my_ids}")

    print("[6] 改期（先定新再放旧）")
    r = await call_agent(B, "reschedule", "改到 16 点", employee=EMP1,
                         slots={"booking_id": bid, "new_date": TOMORROW,
                                "new_start_time": "16:00", "new_end_time": "17:00"})
    new_bid = (_data(r).get("booking") or {}).get("booking_id")
    check("改期完成且产生新凭证", r["state"] == "completed" and bool(new_bid) and new_bid != bid,
          f"state={r['state']} new={new_bid} result={str(r.get('result'))[:200]}")

    print("[7] 改期后列表：新单在、旧单不在")
    r = await call_agent(Q, "my_bookings", "我的预订", employee=EMP1)
    my_ids = [b["booking_id"] for b in _data(r).get("bookings", [])]
    check("列表校验", new_bid in my_ids and bid not in my_ids, f"ids={my_ids}")

    print("[8] 越权取消 → 业务拒绝")
    r = await call_agent(B, "cancel", "取消预订", employee=EMP2,
                         slots={"booking_id": new_bid})
    answer = (r.get("result") or {}).get("answer", "")
    check("他人取消被拒", r["state"] == "completed" and "只能" in answer, f"answer={answer}")

    print("[9] 本人取消 → 成功")
    r = await call_agent(B, "cancel", "取消预订", employee=EMP1,
                         slots={"booking_id": new_bid})
    answer = (r.get("result") or {}).get("answer", "")
    check("本人取消成功", r["state"] == "completed" and "已取消" in answer, f"answer={answer}")

    print("[10] 预订缺房间 → input-required")
    r = await call_agent(B, "book", "订个会议室", employee=EMP1,
                         slots={"date": TOMORROW, "start_time": "15:00", "end_time": "16:00"})
    check("缺房间返回追问", r["state"] == "input_required"
          and "会议室编号" in r.get("message", ""), f"state={r['state']} msg={r.get('message')}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
