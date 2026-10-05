# scripts/check_kb_rag.py
# P6 验收（1/2）：知识库 RAG Agent（直连 A2A）
#   1 报销流程（答案含关键信息 + 来源）
#   2 器材借用期限
#   3 报修 SLA
#   4 知识库没有的问题 → 拒答
#   5 空问题 → input-required
#
# 前置：Milvus(19532) / 已跑 init_office_milvus + build_office_kb / RAG Agent(5015) 已启动
# 运行：python scripts/check_kb_rag.py

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.core.a2a_client import call_agent  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

settings = get_settings()
RAG = settings.rag_agent_url      # :5015

passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


def _answer(r: dict) -> str:
    return (r.get("result") or {}).get("answer", "")


def _sources(r: dict) -> list:
    return ((r.get("result") or {}).get("data") or {}).get("sources", []) or []


async def main() -> int:
    print("[1] 报销流程")
    r = await call_agent(RAG, "ask", "报销流程是什么", timeout=60)
    ans = _answer(r)
    check("完成且答案含流程要点", r["state"] == "completed"
          and ("发票" in ans or "报销单" in ans) and ("打款" in ans or "财务" in ans),
          f"state={r['state']} answer={ans[:150]}")
    check("带来源（≥1）", len(_sources(r)) >= 1, str(_sources(r))[:200])

    print("[2] 器材借用期限")
    r = await call_agent(RAG, "ask", "器材借用期限是多久", timeout=60)
    ans = _answer(r)
    check("答案含 7 天/30 天", r["state"] == "completed" and ("7 天" in ans or "30 天" in ans),
          ans[:150])

    print("[3] 报修 SLA")
    r = await call_agent(RAG, "ask", "报修的响应时限是多久", timeout=60)
    ans = _answer(r)
    check("答案含 4 小时/24 小时", r["state"] == "completed"
          and ("4 小时" in ans or "24 小时" in ans), ans[:150])

    print("[4] 知识库没有的问题 → 拒答")
    r = await call_agent(RAG, "ask", "公司年会抽奖规则是什么", timeout=60)
    check("拒答不编造", r["state"] == "completed" and "没有找到" in _answer(r), _answer(r)[:150])

    print("[5] 空问题 → input-required")
    r = await call_agent(RAG, "ask", "", timeout=30)
    check("空问题返回追问", r["state"] == "input_required", f"state={r['state']}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
