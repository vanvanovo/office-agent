# scripts/check_equipment_agents.py
# P5 验收（2/2）：器材查询 / 报修两个 A2A Agent（直连调用）
#   1 查询：类别（静态+动态融合）
#   2 查询：关键词 / 部门
#   3 报修：设备模糊 → 候选追问
#   4 报修：指定资产 → 成功出单（SLA）
#   5 报修：缺故障描述 → 追问
#   6 报修：编号不存在 → 追问
#   7 进度查询（按工单号）
#   8 我的工单
#   9 催单：越权拒绝 / 本人成功 / 无单可催
#
# 前置：Mock:8210 / 器材 MCP:8112 / 查询 Agent:5013 / 报修 Agent:5014 已启动
# 运行：python scripts/check_equipment_agents.py

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.core.a2a_client import call_agent  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
EQ = settings.equipment_query_agent_url      # :5013
ER = settings.equipment_repair_agent_url     # :5014
EMP1 = {"id": "E1001", "role": "employee"}
EMP2 = {"id": "E1002", "role": "employee"}
EMP4 = {"id": "E1004", "role": "employee"}   # E1004 无种子工单，用于"无单"用例

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
    print("[1] 查询：类别")
    r = await call_agent(EQ, "query", "设计部还有几台投影仪可以借", employee=EMP1,
                         slots={"category": "投影仪"})
    items = _data(r).get("equipment", [])
    p1 = next((i for i in items if i["asset_id"] == "P001"), {})
    check("完成且 5 台投影仪", r["state"] == "completed" and len(items) == 5, f"len={len(items)}")
    check("回答含 P001 与可借统计", "P001" in (r.get("result") or {}).get("answer", "")
          and "可借" in (r.get("result") or {}).get("answer", ""), str(r.get("result"))[:150])
    check("P001 动态状态=已借出", p1.get("borrowable_now") is False and p1.get("holder") == "李四",
          str(p1))

    print("[2] 查询：关键词 / 部门")
    r = await call_agent(EQ, "query", "打印机在哪", employee=EMP1, slots={"keyword": "打印机"})
    check("关键词命中 2 台", len(_data(r).get("equipment", [])) == 2, str(r.get("result"))[:150])
    r = await call_agent(EQ, "query", "设计部的VR", employee=EMP1,
                         slots={"category": "VR头显", "department": "设计部"})
    v = _data(r).get("equipment", [])
    check("设计部 VR 4 台且 V001 借出",
          len(v) == 4 and any(i["asset_id"] == "V001" and i["borrowable_now"] is False for i in v),
          str(v)[:150])

    print("[3] 报修：设备模糊 → 候选追问")
    r = await call_agent(ER, "repair", "投影仪屏幕碎了帮我报修", employee=EMP1,
                         slots={"keyword": "投影仪", "fault_desc": "屏幕碎了"})
    check("多候选返回追问并列出候选", r["state"] == "input_required"
          and "多台" in r.get("message", "") and "P001" in r.get("message", ""),
          f"state={r['state']} msg={r.get('message')}")

    print("[4] 报修：指定资产 → 出单")
    idem = f"agent-eq-{uuid.uuid4().hex[:8]}"
    r = await call_agent(ER, "repair", "P004 画面偏色，报修", employee=EMP1,
                         slots={"asset_id": "P004", "fault_desc": "画面偏色",
                                "urgency": "high", "idempotency_key": idem})
    ticket = _data(r).get("ticket", {}) or {}
    tno = ticket.get("ticket_no")
    check("报修成功（含工单号与 SLA）", r["state"] == "completed" and bool(tno)
          and bool(ticket.get("sla_due_at")), str(r.get("result"))[:200])
    if tno:
        print(f"      ticket_no = {tno}")

    print("[5] 报修：缺故障描述 → 追问")
    r = await call_agent(ER, "repair", "帮我报修", employee=EMP1, slots={"asset_id": "P004"})
    check("缺故障描述返回追问", r["state"] == "input_required"
          and "故障" in r.get("message", ""), f"state={r['state']} msg={r.get('message')}")

    print("[6] 报修：编号不存在 → 追问")
    r = await call_agent(ER, "repair", "XX999 坏了", employee=EMP1,
                         slots={"asset_id": "XX999", "fault_desc": "坏了"})
    check("不存在编号返回追问", r["state"] == "input_required"
          and "没找到" in r.get("message", ""), f"state={r['state']} msg={r.get('message')}")

    print("[7] 进度查询（按工单号）")
    if tno:
        r = await call_agent(ER, "progress", "工单进度", employee=EMP1, slots={"ticket_id": tno})
        check("进度返回待处理", r["state"] == "completed" and "待处理" in (r.get("result") or {}).get("answer", ""),
              str(r.get("result"))[:150])
    else:
        check("进度返回待处理", False, "缺少 ticket_no")

    print("[8] 我的工单")
    r = await call_agent(ER, "my_tickets", "我的工单", employee=EMP1)
    tks = _data(r).get("tickets", [])
    check("我的工单包含新单", bool(tno) and tno in [t["ticket_no"] for t in tks], f"len={len(tks)}")

    print("[9] 催单：越权 / 本人 / 无单")
    if tno:
        r = await call_agent(ER, "urge", "催一下", employee=EMP2, slots={"ticket_id": tno})
        check("越权催单被拒", r["state"] == "completed"
              and "只能" in (r.get("result") or {}).get("answer", ""),
              str(r.get("result"))[:150])
        r = await call_agent(ER, "urge", "催一下", employee=EMP1, slots={"ticket_id": tno})
        check("本人催单成功", r["state"] == "completed"
              and "已催单" in (r.get("result") or {}).get("answer", ""),
              str(r.get("result"))[:150])
    else:
        check("催单用例", False, "缺少 ticket_no")
    r = await call_agent(ER, "progress", "我的工单进度", employee=EMP4)
    check("无工单员工返回无单提示", r["state"] == "completed"
          and "还没有" in (r.get("result") or {}).get("answer", ""),
          str(r.get("result"))[:150])

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
