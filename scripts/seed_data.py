# scripts/seed_data.py
# 灌演示种子数据（可重复执行：先清后插）
#   office_oa    —— 10 间会议室 + 若干预订 + 30 条资产动态状态
#   office_asset —— 30 件器材静态台账
#   office_app   —— 7 名员工（含 2 名管理员；演示密码 123456）
#
# 运行：python scripts/seed_data.py

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bcrypt  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
DAY_AFTER = TODAY + timedelta(days=2)

ROOMS = [
    ("A-301", "A", 3, "A栋301 小会议室", 6, ["投影"]),
    ("A-302", "A", 3, "A栋302 会议室", 10, ["投影", "视频会议"]),
    ("A-401", "A", 4, "A栋401 会议室", 10, ["投影"]),
    ("A-501", "A", 5, "A栋501 会议室", 10, ["视频会议"]),
    ("A-801", "A", 8, "A栋801 会议室", 12, ["视频会议"]),
    ("A-802", "A", 8, "A栋802 大会议室", 20, ["投影", "视频会议"]),
    ("B-201", "B", 2, "B栋201 会议室", 8, ["投影"]),
    ("B-202", "B", 2, "B栋202 会议室", 16, ["视频会议"]),
    ("B-601", "B", 6, "B栋601 小会议室", 6, []),
    ("B-602", "B", 6, "B栋602 多功能厅", 30, ["投影", "视频会议", "白板"]),
]

# (date, room, start, end, employee, purpose, idem_key)
BOOKINGS = [
    (TOMORROW, "A-801", "10:00", "11:00", "E1002", "项目评审", "seed-1"),
    (TOMORROW, "A-501", "15:00", "16:00", "E1004", "部门例会", "seed-2"),
    (TOMORROW, "B-202", "14:00", "15:00", "E1003", "客户沟通", "seed-3"),
    (TODAY, "A-302", "09:30", "10:30", "E1001", "晨会", "seed-4"),
]

EMPLOYEES = [
    ("E1001", "张三", "employee", "研发部"),
    ("E1002", "李四", "employee", "设计部"),
    ("E1003", "王五", "employee", "市场部"),
    ("E1004", "赵六", "employee", "财务部"),
    ("E1005", "孙七", "employee", "行政部"),
    ("A9001", "行政小王", "admin", "行政部"),
    ("A9002", "IT小刘", "admin", "IT部"),
]

# (asset_id, name, category, brand, model, department, status, borrowable)
EQUIPMENT = [
    ("P001", "爱普生投影仪", "投影仪", "爱普生", "CB-FH06", "行政部", "in_stock", 1),
    ("P002", "明基投影仪", "投影仪", "明基", "E540", "行政部", "in_stock", 1),
    ("P003", "坚果便携投影仪", "投影仪", "坚果", "P5", "行政部", "in_stock", 1),
    ("P004", "极米投影仪", "投影仪", "极米", "Z6X", "行政部", "in_stock", 1),
    ("P005", "索尼投影仪", "投影仪", "索尼", "VPL-EX", "行政部", "in_stock", 1),
    ("N001", "联想笔记本", "笔记本电脑", "联想", "ThinkPad T14", "研发部", "in_stock", 1),
    ("N002", "联想笔记本", "笔记本电脑", "联想", "小新 Pro14", "研发部", "in_stock", 1),
    ("N003", "戴尔笔记本", "笔记本电脑", "戴尔", "Latitude 5440", "研发部", "in_stock", 1),
    ("N004", "戴尔笔记本", "笔记本电脑", "戴尔", "XPS 13", "研发部", "in_stock", 1),
    ("N005", "华为笔记本", "笔记本电脑", "华为", "MateBook 14", "研发部", "in_stock", 1),
    ("N006", "联想笔记本", "笔记本电脑", "联想", "ThinkBook 16", "研发部", "issued_to_staff", 0),
    ("N007", "惠普笔记本", "笔记本电脑", "惠普", "战66", "研发部", "in_stock", 1),
    ("N008", "苹果笔记本", "笔记本电脑", "苹果", "MacBook Air", "研发部", "issued_to_staff", 0),
    ("V001", "Meta Quest 3", "VR头显", "Meta", "Quest 3", "设计部", "in_stock", 1),
    ("V002", "PICO VR 一体机", "VR头显", "PICO", "PICO 4", "设计部", "in_stock", 1),
    ("V003", "HTC Vive", "VR头显", "HTC", "Vive Focus 3", "设计部", "in_stock", 1),
    ("V004", "Meta Quest 2", "VR头显", "Meta", "Quest 2", "设计部", "in_stock", 1),
    ("M001", "戴尔显示器 27寸", "显示器", "戴尔", "U2723QE", "研发部", "in_stock", 1),
    ("M002", "戴尔显示器 27寸", "显示器", "戴尔", "U2723QE", "研发部", "in_stock", 1),
    ("M003", "明基显示器 24寸", "显示器", "明基", "GW2480", "研发部", "in_stock", 1),
    ("M004", "AOC 显示器 27寸", "显示器", "AOC", "Q27G2S", "研发部", "in_stock", 1),
    ("M005", "LG 显示器 32寸", "显示器", "LG", "32UN650", "研发部", "in_stock", 1),
    ("M006", "海信显示器 24寸", "显示器", "海信", "24N3G", "研发部", "issued_to_staff", 0),
    ("C001", "佳能相机", "相机", "佳能", "EOS R50", "市场部", "in_stock", 1),
    ("C002", "索尼相机", "相机", "索尼", "Alpha 6400", "市场部", "in_stock", 1),
    ("C003", "大疆口袋云台相机", "相机", "大疆", "Osmo Pocket 3", "市场部", "in_stock", 1),
    ("T001", "iPad 平板", "平板", "苹果", "iPad 10", "市场部", "in_stock", 1),
    ("T002", "华为平板", "平板", "华为", "MatePad 11", "市场部", "in_stock", 1),
    ("T003", "小米平板", "平板", "小米", "Pad 6", "市场部", "in_stock", 1),
    ("T004", "三星平板", "平板", "三星", "Tab S9", "市场部", "in_stock", 1),
    ("PR001", "惠普打印机", "打印机", "惠普", "LaserJet Pro", "行政部", "in_stock", 1),
    ("PR002", "佳能打印机", "打印机", "佳能", "imageCLASS", "行政部", "in_stock", 1),
]

# 动态状态：(asset_id, borrowable_now, holder, due_back)
ASSET_DYNAMIC_BORROWED = {
    "P001": ("李四", datetime.combine(TOMORROW, datetime.strptime("18:00", "%H:%M").time())),
    "P002": ("王五", datetime.combine(DAY_AFTER, datetime.strptime("12:00", "%H:%M").time())),
    "V001": ("赵六", datetime.combine(TOMORROW, datetime.strptime("12:00", "%H:%M").time())),
    "N006": ("张三", None),
    "N008": ("李四", None),
    "M006": ("孙七", None),
}


def _hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


async def _seed_oa(engine) -> tuple[int, int, int, int]:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM bookings"))
        await conn.execute(text("DELETE FROM rooms"))
        await conn.execute(text("DELETE FROM asset_availability"))
        await conn.execute(text("DELETE FROM asset_ledger_source"))

        await conn.execute(text("""
            INSERT INTO rooms (room_id, building, floor, name, capacity, equipment, active)
            VALUES (:room_id, :building, :floor, :name, :capacity, :equipment, 1)
        """), [{"room_id": r[0], "building": r[1], "floor": r[2], "name": r[3],
                "capacity": r[4], "equipment": json.dumps(r[5], ensure_ascii=False)}
               for r in ROOMS])

        booking_rows = []
        for i, (d, room, s, e, emp, purpose, key) in enumerate(BOOKINGS):
            booking_rows.append({
                "bid": f"BK{d.strftime('%Y%m%d')}-SEED{i + 1:02d}",
                "room": room, "d": d.isoformat(), "s": s, "e": e,
                "emp": emp, "purpose": purpose, "key": key,
            })
        await conn.execute(text("""
            INSERT INTO bookings (booking_id, room_id, date, start_time, end_time,
                                  employee_id, purpose, status, idempotency_key)
            VALUES (:bid, :room, :d, :s, :e, :emp, :purpose, 'booked', :key)
        """), booking_rows)

        avail_rows = []
        for (asset_id, *_rest) in EQUIPMENT:
            if asset_id in ASSET_DYNAMIC_BORROWED:
                holder, due = ASSET_DYNAMIC_BORROWED[asset_id]
                avail_rows.append({"a": asset_id, "b": 0, "h": holder, "due": due})
            else:
                avail_rows.append({"a": asset_id, "b": 1, "h": None, "due": None})
        await conn.execute(text("""
            INSERT INTO asset_availability (asset_id, borrowable_now, holder, due_back)
            VALUES (:a, :b, :h, :due)
        """), avail_rows)

        # 外部资产系统的「台账源」（每日同步任务的来源；模拟甲方资产系统）
        await conn.execute(text("""
            INSERT INTO asset_ledger_source
                (asset_id, name, category, brand, model, department, owner_id, status, borrowable)
            VALUES (:a, :n, :c, :br, :mo, :dep, NULL, :st, :bk)
        """), [{"a": a, "n": n, "c": c, "br": br, "mo": mo, "dep": dep, "st": st, "bk": bk}
               for (a, n, c, br, mo, dep, st, bk) in EQUIPMENT])

    return len(ROOMS), len(BOOKINGS), len(EQUIPMENT), len(EQUIPMENT)


async def _seed_asset(engine) -> int:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM equipment"))
        await conn.execute(text("DELETE FROM repair_tickets"))
        await conn.execute(text("""
            INSERT INTO equipment (asset_id, name, category, brand, model, department,
                                   owner_id, status, borrowable)
            VALUES (:a, :n, :c, :br, :mo, :dep, NULL, :st, :bk)
        """), [{"a": a, "n": n, "c": c, "br": br, "mo": mo, "dep": dep, "st": st, "bk": bk}
               for (a, n, c, br, mo, dep, st, bk) in EQUIPMENT])
        # 演示用报修工单（含一条已超 SLA 的，供站内提醒/报表演示）
        now = datetime.now()
        tickets = [
            ("RT20261002-DEMO", "N001", "键盘部分按键失灵", "medium", "E1001", "open",
             now - timedelta(days=3), now - timedelta(days=2)),      # 已超 SLA
            ("RT20261003-DEMO", "V001", "头显镜片有划痕", "low", "E1005", "in_progress",
             now - timedelta(days=2), now + timedelta(days=1)),
            ("RT20261004-DEMO", "P001", "风扇异响", "medium", "E1002", "done",
             now - timedelta(days=1), now + timedelta(hours=20)),
        ]
        await conn.execute(text("""
            INSERT INTO repair_tickets (ticket_no, asset_id, fault_desc, urgency, reporter_id,
                                        status, attachments, idempotency_key, sla_due_at,
                                        created_at, updated_at)
            VALUES (:t, :a, :f, :u, :r, :st, NULL, NULL, :sla, :created, :created)
        """), [{"t": t, "a": a, "f": f, "u": u, "r": r, "st": st, "sla": sla, "created": created}
               for (t, a, f, u, r, st, created, sla) in tickets])
    return len(EQUIPMENT)


async def _seed_app(engine) -> int:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM employees"))
        await conn.execute(text("""
            INSERT INTO employees (emp_id, name, role, password_hash, department)
            VALUES (:emp, :name, :role, :pw, :dep)
        """), [{"emp": e, "name": n, "role": r, "pw": _hash_pw("123456"), "dep": d}
               for (e, n, r, d) in EMPLOYEES])
        # 审计/站内信/兜底池清空（演示从干净状态开始）
        await conn.execute(text("DELETE FROM write_audit"))
        await conn.execute(text("DELETE FROM station_messages"))
        await conn.execute(text("DELETE FROM unresolved_requests"))
    return len(EMPLOYEES)


async def main() -> None:
    configure_logging()
    s = get_settings()
    engines = {
        "oa": create_async_engine(s.mysql_oa_url),
        "asset": create_async_engine(s.mysql_asset_url),
        "app": create_async_engine(s.mysql_app_url),
    }
    try:
        rooms, bookings, assets, ledger = await _seed_oa(engines["oa"])
        equip = await _seed_asset(engines["asset"])
        emps = await _seed_app(engines["app"])
        print(f"[seed] office_oa   : {rooms} 会议室 / {bookings} 预订 / {assets} 条动态状态 / {ledger} 条台账源")
        print(f"[seed] office_asset: {equip} 件器材（同步前的旧台账）")
        print(f"[seed] office_app : {emps} 名员工（演示密码 123456）")
        logger.info("seed.done", rooms=rooms, bookings=bookings, equipment=equip, employees=emps)
        print("[done] 种子数据就绪")
    finally:
        for e in engines.values():
            await e.dispose()


if __name__ == "__main__":
    asyncio.run(main())
