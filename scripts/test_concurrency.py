# scripts/test_concurrency.py
# 并发预订断言（P1 门禁核心）：
#   两个请求同时预订同一会议室同一时段 → 必须恰好一人成功、一人 409。
#
# 前置：Mock 服务已启动（python services/mock_internal/main.py）
# 运行：python scripts/test_concurrency.py

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from backend.config import get_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

BASE = get_settings().mock_internal_url
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


async def main() -> int:
    # 独立时段（11:00-12:00），避免与功能检查脚本互相干扰
    slot = {"room_id": "B-601", "date": TOMORROW, "start_time": "11:00", "end_time": "12:00"}

    async with httpx.AsyncClient(base_url=BASE, timeout=15) as c:
        reqs = [
            c.post("/oa/bookings", json={**slot, "employee_id": "E1001", "purpose": "并发测试A",
                                         "idempotency_key": f"conc-{uuid.uuid4().hex[:8]}"}),
            c.post("/oa/bookings", json={**slot, "employee_id": "E1002", "purpose": "并发测试B",
                                         "idempotency_key": f"conc-{uuid.uuid4().hex[:8]}"}),
        ]
        r1, r2 = await asyncio.gather(*reqs)

    codes = sorted([r1.status_code, r2.status_code])
    print(f"请求A: {r1.status_code} | 请求B: {r2.status_code}")

    ok = codes == [201, 409]
    print(("[PASS] " if ok else "[FAIL] ") + "断言：恰好一人成功（201），一人冲突（409）")

    # 清理：取消成功的那一单
    winner = r1 if r1.status_code == 201 else r2
    if winner.status_code == 201:
        booking_id = winner.json()["booking"]["booking_id"]
        emp = "E1001" if winner is r1 else "E1002"
        async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
            rc = await c.post(f"/oa/bookings/{booking_id}/cancel", json={"employee_id": emp})
        print(f"清理：取消 {booking_id} → {rc.status_code}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
