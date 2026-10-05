# scripts/remind_once.py
# 手动跑一次提醒任务（演示/巡检用）：
#   python scripts/remind_once.py
#
# 正常运行时由网关内的 APScheduler 每 60 秒自动执行。

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.logger import configure_logging  # noqa: E402
from backend.core.reminders import run_meeting_reminders, run_ticket_reminders  # noqa: E402


async def main() -> int:
    configure_logging()
    meeting = await run_meeting_reminders()
    tickets = await run_ticket_reminders()
    print(f"[remind] 会前提醒新建 {meeting} 条 / 工单超时提醒新建 {tickets} 条")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
