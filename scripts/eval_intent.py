# scripts/eval_intent.py
# 会议域意图评测：对每条评测样本调用 Router 的 LLM 意图识别，统计：
#   - 意图完全匹配率（intents 集合完全一致）
#   - 追问判定准确率（need_clarify 一致）
#   - 槽位准确率（样本声明的 expected_slots 全部命中）
# 输出：控制台摘要 + docs/p4_intent_eval_report.md
#
# 运行：python scripts/eval_intent.py

import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import router as intent_router  # noqa: E402
from backend.core.session import Session  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "eval" / "office_intents.json"
REPORT = ROOT / "docs" / "office_intent_eval_report.md"
CONCURRENCY = 4

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _norm(v):
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    return str(v).strip()


def _slot_match(result_slots: dict, expected: dict) -> tuple[bool, list[str]]:
    misses = []
    for k, v in expected.items():
        if isinstance(v, str) and v == "TOMORROW":
            v = TOMORROW
        actual = result_slots.get(k)
        if _norm(actual) != _norm(v):
            misses.append(f"{k}: 期望={v!r} 实际={actual!r}")
    return (not misses), misses


async def run_one(item: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        session = Session(session_id="eval", emp_id="E1001", role="employee", name="张三")
        t0 = time.time()
        try:
            result = await intent_router.classify(item["text"], session)
            error = ""
        except Exception as e:  # noqa: BLE001
            result = {"intents": [], "slots": {}, "need_clarify": False}
            error = str(e)[:150]
        elapsed = int((time.time() - t0) * 1000)

    intents_ok = set(result["intents"]) == set(item["expected_intents"])
    clarify_ok = bool(result["need_clarify"]) == bool(item["expected_need_clarify"])
    slots_ok, slot_miss = _slot_match(result.get("slots") or {}, item.get("expected_slots") or {})

    return {
        "id": item["id"], "category": item["category"], "text": item["text"],
        "expected_intents": sorted(item["expected_intents"]),
        "actual_intents": sorted(result["intents"]),
        "expected_need_clarify": item["expected_need_clarify"],
        "actual_need_clarify": bool(result["need_clarify"]),
        "intents_ok": intents_ok, "clarify_ok": clarify_ok,
        "slots_ok": slots_ok, "slot_miss": slot_miss,
        "actual_slots": result.get("slots") or {},
        "elapsed_ms": elapsed, "error": error,
    }


async def main() -> int:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    items = data["items"]
    sem = asyncio.Semaphore(CONCURRENCY)

    print(f"评测集：{DATASET.name} v{data.get('version')} | {len(items)} 条 | 并发 {CONCURRENCY}")
    t0 = time.time()
    results = await asyncio.gather(*[run_one(it, sem) for it in items])
    total_sec = time.time() - t0

    n = len(results)
    intents_acc = sum(r["intents_ok"] for r in results) / n
    clarify_acc = sum(r["clarify_ok"] for r in results) / n
    slot_results = []
    for r, it in zip(results, items):
        if it.get("expected_slots"):
            slot_results.append(r)
    slots_acc = (sum(r["slots_ok"] for r in slot_results) / len(slot_results)) if slot_results else 1.0

    categories = {}
    for r in results:
        c = categories.setdefault(r["category"], {"n": 0, "intents": 0, "clarify": 0})
        c["n"] += 1
        c["intents"] += int(r["intents_ok"])
        c["clarify"] += int(r["clarify_ok"])

    print(f"\n总样本：{n} | 耗时 {total_sec:.0f}s（平均 {total_sec / n:.1f}s/条）")
    print(f"意图完全匹配率：{intents_acc:.1%}")
    print(f"追问判定准确率：{clarify_acc:.1%}")
    print(f"槽位准确率（{len(slot_results)} 条有槽位断言）：{slots_acc:.1%}")
    for name, c in categories.items():
        print(f"  [{name}] {c['intents']}/{c['n']} 意图 | {c['clarify']}/{c['n']} 追问")

    fails = [r for r in results if not (r["intents_ok"] and r["clarify_ok"] and r["slots_ok"])]
    if fails:
        print(f"\n未全对样本（{len(fails)}）：")
        for r in fails:
            print(f"  {r['id']} [{r['category']}] {r['text']}")
            if not r["intents_ok"]:
                print(f"      意图 期望={r['expected_intents']} 实际={r['actual_intents']}")
            if not r["clarify_ok"]:
                print(f"      追问 期望={r['expected_need_clarify']} 实际={r['actual_need_clarify']}")
            if not r["slots_ok"]:
                print(f"      槽位 {'; '.join(r['slot_miss'])}")

    # 写报告
    lines = [
        "# P4 意图评测报告（会议域）",
        "",
        f"> 评测集：`data/eval/meeting_intents.json` v{data.get('version')}（{n} 条：正常/模糊/越界/多意图）",
        f"> 被测对象：`backend/core/router.py` LLM 意图识别（DeepSeek，提示词 {intent_router.INTENT_PROMPT_VERSION}）",
        f"> 运行日期：{date.today().isoformat()} | 总耗时 {total_sec:.0f}s（平均 {total_sec / n:.1f}s/条，并发 {CONCURRENCY}）",
        "",
        "## 指标总览",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 意图完全匹配率 | **{intents_acc:.1%}**（{sum(r['intents_ok'] for r in results)}/{n}） |",
        f"| 追问判定准确率 | **{clarify_acc:.1%}**（{sum(r['clarify_ok'] for r in results)}/{n}） |",
        f"| 槽位准确率 | **{slots_acc:.1%}**（{len(slot_results)} 条有槽位断言） |",
        "",
        "## 分类别",
        "",
        "| 类别 | 样本数 | 意图匹配 | 追问判定 |",
        "|---|---|---|---|",
    ]
    for name, c in categories.items():
        lines.append(f"| {name} | {c['n']} | {c['intents']}/{c['n']} | {c['clarify']}/{c['n']} |")
    if fails:
        lines += ["", "## 未全对样本", "", "| id | 类别 | 输入 | 期望意图 | 实际意图 | 说明 |", "|---|---|---|---|---|---|"]
        for r in fails:
            note = []
            if not r["intents_ok"]:
                note.append("意图不匹配")
            if not r["clarify_ok"]:
                note.append(f"追问期望{r['expected_need_clarify']}实际{r['actual_need_clarify']}")
            if not r["slots_ok"]:
                note.append("槽位：" + "；".join(r["slot_miss"]))
            lines.append(f"| {r['id']} | {r['category']} | {r['text']} | {r['expected_intents']} | {r['actual_intents']} | {'；'.join(note)} |")
    else:
        lines += ["", "全部样本三指标（意图/追问/槽位）全对。"]
    lines += ["", "> 说明：失败样本已如实记录；提示词或槽位规则调整后必须重跑本评测集（评测就是 AI 项目的回归测试）。", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入：{REPORT}")

    return 0 if (intents_acc >= 0.85 and clarify_acc >= 0.85) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
