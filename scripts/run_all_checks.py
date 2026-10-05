# scripts/run_all_checks.py
# 全量回归：依次运行所有验收脚本，汇总结果（评测/回归就是 AI 项目的测试门禁）。
#
# 运行：python scripts/run_all_checks.py
# 预计：6~10 分钟（含 LLM 评测）

import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")   # 兼容 GBK 控制台

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    "check_mock_oa.py",
    "test_concurrency.py",
    "check_oa_mcp.py",
    "check_asset_mcp.py",
    "check_agents.py",
    "check_equipment_agents.py",
    "check_chat.py",
    "check_chat_v2.py",
    "check_chat_v3.py",
    "check_kb_rag.py",
    "check_reports.py",
    "check_reminders.py",
    "check_recurring.py",
    "check_channel.py",
    "security_test_sql_injection.py",
    "security/legacy_free_sql_demo.py",
    "eval_intent.py",
]


def main() -> int:
    results = []
    t0 = time.time()
    logdir = ROOT / "logs" / "checks"
    logdir.mkdir(parents=True, exist_ok=True)
    for name in CHECKS:
        script = ROOT / "scripts" / name
        print(f"\n===== {name} =====")
        started = time.time()
        r = subprocess.run([sys.executable, str(script)], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        # 子脚本完整输出落盘（便于排查波动/失败项）
        (logdir / f"{name.replace('/', '_')}.log").write_text(
            (r.stdout or "") + "\n--- stderr ---\n" + (r.stderr or ""), encoding="utf-8")
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        summary = tail[-1] if tail else "(no output)"
        ok = r.returncode == 0
        results.append((name, ok, round(time.time() - started, 1), summary))
        print(summary)
        if not ok:
            print(f"[stderr] {(r.stderr or '')[-500:]}")

    print("\n================ 全量回归汇总 ================")
    print(f"{'脚本':<34}{'结果':<6}{'耗时(s)':<9}摘要")
    for name, ok, sec, summary in results:
        print(f"{name:<34}{'PASS' if ok else 'FAIL':<6}{sec:<9}{summary}")

    passed = sum(1 for _, ok, _, _ in results if ok)
    print(f"\n总计：{passed}/{len(results)} 通过 | 总耗时 {time.time() - t0:.0f}s")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
