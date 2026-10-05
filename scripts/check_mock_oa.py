# scripts/check_mock_oa.py
# Mock 内部系统功能验收（P1 门禁）：
#   查询 / 可用时段 / 预订 / 幂等 / 越权拒绝 / 取消释放时段 / 改期（先定新再放旧）/ 我的预订 / 报表 / 资产状态
#
# 前置：Mock 服务已启动（python services/mock_internal/main.py）
# 运行：python scripts/check_mock_oa.py

import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from backend.config import get_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")   # 防止 GBK 控制台遇到特殊字符崩溃

BASE = get_settings().mock_internal_url
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} | {detail}")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=10)

    print("[0] 清理上次遗留的测试预订（幂等）")
    my = c.get("/oa/bookings/my", params={"employee_id": "E1001"}).json().get("bookings", [])
    cleaned = 0
    for b in my:
        if b.get("purpose") in ("验收测试", "MCP 验收"):
            c.post(f"/oa/bookings/{b['booking_id']}/cancel", json={"employee_id": "E1001"})
            cleaned += 1
    print(f"      清理 {cleaned} 条")

    print("[1] 健康检查与基础查询")
    r = c.get("/health")
    check("health 200", r.status_code == 200, r.text)
    r = c.get("/oa/rooms")
    rooms = r.json()
    check("10 间会议室", r.status_code == 200 and len(rooms) == 10, f"count={len(rooms)}")

    print("[2] 可用时段查询（明天 15:00-16:00 A 栋）")
    r = c.get("/oa/availability", params={"date": TOMORROW, "start_time": "15:00",
                                          "end_time": "16:00", "building": "A"})
    avail = r.json()["rooms"]
    ids = [x["room_id"] for x in avail]
    check("A-501 被占用不在可用列表（种子数据）", "A-501" not in ids, f"ids={ids}")
    check("A-801 在可用列表", "A-801" in ids, f"ids={ids}")
    check("全部为 A 栋", all(x["building"] == "A" for x in avail))

    print("[3] 预订 + 幂等")
    idem = f"check-{uuid.uuid4().hex[:8]}"
    payload = {"room_id": "B-601", "date": TOMORROW, "start_time": "16:00", "end_time": "17:00",
               "employee_id": "E1001", "purpose": "验收测试", "idempotency_key": idem}
    r1 = c.post("/oa/bookings", json=payload)
    check("首次预订 201", r1.status_code == 201, r1.text)
    booking_id = r1.json()["booking"]["booking_id"]
    r2 = c.post("/oa/bookings", json=payload)
    check("同幂等键返回同一单", r2.status_code in (200, 201)
          and r2.json()["booking"]["booking_id"] == booking_id, r2.text)

    print("[4] 重复时段冲突 409")
    r3 = c.post("/oa/bookings", json={**payload, "employee_id": "E1002",
                                      "idempotency_key": f"check-{uuid.uuid4().hex[:8]}"})
    check("同房同时段被拒 409", r3.status_code == 409, r3.text)

    print("[5] 越权取消被拒 + 本人取消 + 时段释放")
    r4 = c.post(f"/oa/bookings/{booking_id}/cancel", json={"employee_id": "E1002"})
    check("他人取消 → 403", r4.status_code == 403, r4.text)
    r5 = c.post(f"/oa/bookings/{booking_id}/cancel", json={"employee_id": "E1001"})
    check("本人取消 → 200", r5.status_code == 200, r5.text)
    r6 = c.post("/oa/bookings", json={**payload, "idempotency_key": f"check-{uuid.uuid4().hex[:8]}"})
    check("取消后时段可重新预订", r6.status_code == 201, r6.text)
    reb_id = r6.json()["booking"]["booking_id"]

    print("[6] 改期（先定新再放旧）")
    r7 = c.post(f"/oa/bookings/{reb_id}/reschedule",
                json={"new_date": TOMORROW, "new_start_time": "17:00", "new_end_time": "18:00",
                      "employee_id": "E1001", "idempotency_key": f"check-{uuid.uuid4().hex[:8]}"})
    check("改期成功", r7.status_code == 200, r7.text)
    new_id = r7.json()["booking"]["booking_id"]
    old = c.get(f"/oa/bookings/{reb_id}").json()
    check("旧单已取消", old["status"] == "canceled", str(old))
    r8 = c.get("/oa/availability", params={"date": TOMORROW, "start_time": "16:00",
                                           "end_time": "17:00", "building": "B"})
    check("原时段已释放", "B-601" in [x["room_id"] for x in r8.json()["rooms"]])

    print("[7] 我的预订 / 报表 / 资产状态")
    r9 = c.get("/oa/bookings/my", params={"employee_id": "E1001"})
    check("我的预订包含新单", new_id in [x["booking_id"] for x in r9.json()["bookings"]], r9.text)

    # 清理：删除本次测试产生的活跃预订（保持脚本可重复运行）
    c.post(f"/oa/bookings/{new_id}/cancel", json={"employee_id": "E1001"})
    print(f"      清理：已取消 {new_id}")
    r10 = c.get("/oa/reports/room_usage", params={"from": TOMORROW, "to": TOMORROW})
    check("报表返回 10 间房", len(r10.json()["rooms"]) == 10, r10.text)
    r11 = c.get("/asset/availability", params={"asset_ids": "P001,P003,V001"})
    states = {x["asset_id"]: x for x in r11.json()}
    check("P001 已借出 / P003 可借 / V001 已借出",
          states["P001"]["borrowable_now"] == 0 and states["P003"]["borrowable_now"] == 1
          and states["V001"]["borrowable_now"] == 0, str(states))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
