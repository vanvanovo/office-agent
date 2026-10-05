# services/mock_internal/main.py
# Mock 内部系统（模拟公司 OA + 资产系统）
#   - OA 模块（V1）：会议室查询 / 可用时段 / 预订 / 改期 / 取消 / 我的预订 / 利用率报表
#   - 资产模块（V2 用，先留接口）：动态可用状态（可借/持有人/归还时间）
#
# 运行：python services/mock_internal/main.py   → http://localhost:8210
#
# 并发正确性：预订/改期在事务内先对 rooms 行加锁（SELECT ... FOR UPDATE），
#             再查重叠时段——从根上避免"两人同时查到可用、都下单成功"。

from __future__ import annotations

import sys
import uuid
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import bindparam, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)
settings = get_settings()
engine = create_async_engine(settings.mysql_oa_url, pool_size=5, max_overflow=10, echo=False)


# ── 工具函数 ──────────────────────────────────────────────

def _parse_date(s: str) -> date_cls:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(400, f"日期格式应为 YYYY-MM-DD：{s}") from e


def _parse_time(s: str) -> str:
    """校验并归一化 HH:MM（返回字符串，MySQL TIME 比较用）。"""
    try:
        t = datetime.strptime(s, "%H:%M")
    except ValueError as e:
        raise HTTPException(400, f"时间格式应为 HH:MM：{s}") from e
    return t.strftime("%H:%M")


def _validate_slot(start: str, end: str) -> tuple[str, str]:
    s, e = _parse_time(start), _parse_time(end)
    if s >= e:
        raise HTTPException(400, "开始时间必须早于结束时间")
    return s, e


def _fmt_time(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, timedelta):          # PyMySQL 把 TIME 读成 timedelta
        total = int(v.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"
    return str(v)[:5]


def _fmt_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (date_cls, datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, timedelta):
            out[k] = _fmt_time(v)
        elif k == "equipment" and isinstance(v, str):
            import json
            try:
                out[k] = json.loads(v)
            except Exception:  # noqa: BLE001
                out[k] = v
        else:
            out[k] = v
    return out


def _new_booking_id(d: date_cls) -> str:
    return f"BK{d.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


async def _get_booking(conn, booking_id: str, for_update: bool = False) -> Optional[dict]:
    sql = "SELECT * FROM bookings WHERE booking_id=:bid"
    if for_update:
        sql += " FOR UPDATE"
    r = await conn.execute(text(sql), {"bid": booking_id})
    row = r.mappings().first()
    return dict(row) if row else None


async def _lock_room(conn, room_id: str) -> Optional[dict]:
    """对房间行加锁（串行化同一房间的预订事务）；返回房间或 None。"""
    r = await conn.execute(
        text("SELECT * FROM rooms WHERE room_id=:rid AND active=1 FOR UPDATE"),
        {"rid": room_id},
    )
    row = r.mappings().first()
    return dict(row) if row else None


async def _overlap_exists(conn, room_id: str, d: str, start: str, end: str,
                          exclude_booking: Optional[str] = None) -> bool:
    """重叠检查必须用「加锁读」（FOR UPDATE）：REPEATABLE READ 下普通 SELECT 读的是旧快照，
    并发时第二个事务会看不到第一个事务刚插入的预订，导致双下单。"""
    sql = ("SELECT booking_id FROM bookings "
           "WHERE room_id=:rid AND date=:d AND status='booked' "
           "AND start_time < :end AND end_time > :start")
    params = {"rid": room_id, "d": d, "start": start, "end": end}
    if exclude_booking:
        sql += " AND booking_id <> :ex"
        params["ex"] = exclude_booking
    sql += " FOR UPDATE"
    r = await conn.execute(text(sql), params)
    return r.first() is not None


# ── 请求模型 ──────────────────────────────────────────────

class BookingCreate(BaseModel):
    room_id: str
    date: str
    start_time: str
    end_time: str
    employee_id: str
    purpose: Optional[str] = None
    idempotency_key: Optional[str] = None


class RescheduleReq(BaseModel):
    new_date: str
    new_start_time: str
    new_end_time: str
    employee_id: str
    idempotency_key: Optional[str] = None


class CancelReq(BaseModel):
    employee_id: str


class RecurringReq(BaseModel):
    room_id: str
    weekday: int                       # 1=周一 ... 7=周日
    start_time: str
    end_time: str
    until: str                         # YYYY-MM-DD（含）
    employee_id: str
    purpose: Optional[str] = None
    idempotency_key: Optional[str] = None


# ── 应用 ──────────────────────────────────────────────────

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("mock_internal.starting", port=8210, db=settings.mysql_db_oa)
    yield
    await engine.dispose()
    logger.info("mock_internal.stopped")


app = FastAPI(title="Mock 内部系统（OA + 资产）", version="1.0.0", lifespan=lifespan)


@app.get("/health", tags=["系统"])
async def health():
    return {"status": "ok", "service": "mock-internal"}


# ── OA：会议室 ────────────────────────────────────────────

@app.get("/oa/rooms", tags=["OA"])
async def list_rooms(building: Optional[str] = None, floor: Optional[int] = None):
    sql = "SELECT * FROM rooms WHERE active=1"
    params: dict[str, Any] = {}
    if building:
        sql += " AND building=:building"
        params["building"] = building
    if floor:
        sql += " AND floor=:floor"
        params["floor"] = floor
    sql += " ORDER BY building, floor, capacity"
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    return [_fmt_row(dict(r)) for r in rows]


@app.get("/oa/availability", tags=["OA"])
async def availability(
    date: str,
    start_time: str,
    end_time: str,
    building: Optional[str] = None,
    floor: Optional[int] = None,
    capacity_min: Optional[int] = None,
):
    """查指定时段可用会议室（实时，不落库）。"""
    d = _parse_date(date).isoformat()
    s, e = _validate_slot(start_time, end_time)
    sql = """
        SELECT r.* FROM rooms r
        WHERE r.active=1
          AND (:building IS NULL OR r.building = :building)
          AND (:floor IS NULL OR r.floor = :floor)
          AND (:cap IS NULL OR r.capacity >= :cap)
          AND r.room_id NOT IN (
              SELECT b.room_id FROM bookings b
              WHERE b.date = :d AND b.status='booked'
                AND b.start_time < :end AND b.end_time > :start
          )
        ORDER BY r.building, r.floor, r.capacity
    """
    params = {"building": building, "floor": floor, "cap": capacity_min,
              "d": d, "start": s, "end": e}
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    return {"date": d, "start_time": s, "end_time": e,
            "rooms": [_fmt_row(dict(r)) for r in rows]}


@app.post("/oa/bookings", status_code=201, tags=["OA"])
async def create_booking(req: BookingCreate):
    d = _parse_date(req.date).isoformat()
    s, e = _validate_slot(req.start_time, req.end_time)
    async with engine.begin() as conn:
        # ① 先锁房间行 → 串行化同一房间的并发预订（锁必须先拿，再做所有检查）
        room = await _lock_room(conn, req.room_id)
        if not room:
            raise HTTPException(404, f"会议室不存在或停用：{req.room_id}")

        # ② 幂等：同一 idempotency_key 直接返回既有单（加锁读，避开 RR 快照陈旧）
        if req.idempotency_key:
            r = await conn.execute(
                text("SELECT * FROM bookings WHERE idempotency_key=:k FOR UPDATE"),
                {"k": req.idempotency_key},
            )
            row = r.mappings().first()
            if row:
                logger.info("mock_booking.idempotent_hit", key=req.idempotency_key)
                return {"idempotent": True, "booking": _fmt_row(dict(row))}

        # ③ 重叠检查（锁内加锁读，结果可信）
        if await _overlap_exists(conn, req.room_id, d, s, e):
            logger.info("mock_booking.conflict", room=req.room_id, date=d, start=s, end=e)
            raise HTTPException(409, f"该时段已被预订：{req.room_id} {d} {s}-{e}")

        # ④ 落单
        booking_id = _new_booking_id(_parse_date(req.date))
        await conn.execute(text("""
            INSERT INTO bookings (booking_id, room_id, date, start_time, end_time,
                                  employee_id, purpose, status, idempotency_key)
            VALUES (:bid, :rid, :d, :s, :e, :emp, :purpose, 'booked', :key)
        """), {"bid": booking_id, "rid": req.room_id, "d": d, "s": s, "e": e,
               "emp": req.employee_id, "purpose": req.purpose, "key": req.idempotency_key})
        logger.info("mock_booking.created", booking_id=booking_id,
                    room=req.room_id, date=d, slot=f"{s}-{e}", employee=req.employee_id)
        booking = await _get_booking(conn, booking_id)
    return {"booking": _fmt_row(booking)}


@app.post("/oa/bookings/{booking_id}/reschedule", tags=["OA"])
async def reschedule_booking(booking_id: str, req: RescheduleReq):
    """改期：先定新时段、成功后再取消旧单（先定新再放旧）。"""
    d = _parse_date(req.new_date).isoformat()
    s, e = _validate_slot(req.new_start_time, req.new_end_time)
    async with engine.begin() as conn:
        pre = await _get_booking(conn, booking_id)
        if not pre:
            raise HTTPException(404, f"预订不存在：{booking_id}")
        room = await _lock_room(conn, pre["room_id"])
        if not room:
            raise HTTPException(404, f"会议室不存在或停用：{pre['room_id']}")
        old = await _get_booking(conn, booking_id, for_update=True)
        if old["employee_id"] != req.employee_id:
            raise HTTPException(403, "只能操作自己的预订")
        if old["status"] != "booked":
            raise HTTPException(409, f"该预订当前状态为 {old['status']}，不可改期")

        if await _overlap_exists(conn, old["room_id"], d, s, e, exclude_booking=booking_id):
            raise HTTPException(409, f"目标时段已被预订：{old['room_id']} {d} {s}-{e}")

        new_id = _new_booking_id(_parse_date(req.new_date))
        await conn.execute(text("""
            INSERT INTO bookings (booking_id, room_id, date, start_time, end_time,
                                  employee_id, purpose, status, idempotency_key)
            VALUES (:bid, :rid, :d, :s, :e, :emp, :purpose, 'booked', :key)
        """), {"bid": new_id, "rid": old["room_id"], "d": d, "s": s, "e": e,
               "emp": req.employee_id, "purpose": old.get("purpose"),
               "key": req.idempotency_key})
        await conn.execute(text("UPDATE bookings SET status='canceled' WHERE booking_id=:bid"),
                           {"bid": booking_id})
        logger.info("mock_booking.rescheduled", old=booking_id, new=new_id,
                    room=old["room_id"], new_slot=f"{d} {s}-{e}")
        new_booking = await _get_booking(conn, new_id)
    return {"old_booking_id": booking_id, "booking": _fmt_row(new_booking)}


@app.post("/oa/bookings/{booking_id}/cancel", tags=["OA"])
async def cancel_booking(booking_id: str, req: CancelReq):
    async with engine.begin() as conn:
        old = await _get_booking(conn, booking_id, for_update=True)
        if not old:
            raise HTTPException(404, f"预订不存在：{booking_id}")
        if old["employee_id"] != req.employee_id:
            raise HTTPException(403, "只能取消自己的预订")
        if old["status"] == "canceled":
            return {"booking_id": booking_id, "status": "canceled", "idempotent": True}
        await conn.execute(text("UPDATE bookings SET status='canceled' WHERE booking_id=:bid"),
                           {"bid": booking_id})
        logger.info("mock_booking.canceled", booking_id=booking_id)
    return {"booking_id": booking_id, "status": "canceled"}


@app.post("/oa/bookings/recurring", tags=["OA"])
async def create_recurring(req: RecurringReq):
    """周期预订：每周固定时段，逐次落单；冲突场次不落单并返回清单。"""
    if not (1 <= req.weekday <= 7):
        raise HTTPException(400, "weekday 取值 1-7（周一=1，周日=7）")
    s, e = _validate_slot(req.start_time, req.end_time)
    until = _parse_date(req.until)
    today = date_cls.today()
    if until < today:
        raise HTTPException(400, "until 不能早于今天")

    dates: list[date_cls] = []
    d = today
    while d <= until:
        if d.isoweekday() == req.weekday:
            dates.append(d)
        d += timedelta(days=1)
    if not dates:
        raise HTTPException(400, "区间内没有匹配的星期")
    if len(dates) > 26:
        raise HTTPException(400, "单次最多 26 次（约半年），请缩小范围")

    series_id = f"SR{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    results: list[dict] = []
    async with engine.begin() as conn:
        room = await _lock_room(conn, req.room_id)
        if not room:
            raise HTTPException(404, f"会议室不存在或停用：{req.room_id}")
        for d in dates:
            ds = d.isoformat()
            if await _overlap_exists(conn, req.room_id, ds, s, e):
                results.append({"date": ds, "status": "conflict"})
                continue
            bid = _new_booking_id(d)
            key = f"{req.idempotency_key}-{ds}" if req.idempotency_key else None
            await conn.execute(text("""
                INSERT INTO bookings (booking_id, room_id, date, start_time, end_time,
                                      employee_id, purpose, status, series_id, idempotency_key)
                VALUES (:bid, :rid, :d, :s, :e, :emp, :purpose, 'booked', :series, :key)
            """), {"bid": bid, "rid": req.room_id, "d": ds, "s": s, "e": e,
                   "emp": req.employee_id, "purpose": req.purpose,
                   "series": series_id, "key": key})
            results.append({"date": ds, "status": "booked", "booking_id": bid})

    booked = sum(1 for r in results if r["status"] == "booked")
    logger.info("mock_booking.recurring_created", series=series_id, room=req.room_id,
                weekday=req.weekday, total=len(results), booked=booked)
    return {"series_id": series_id, "room_id": req.room_id, "weekday": req.weekday,
            "slot": f"{s}-{e}", "until": until.isoformat(),
            "total": len(results), "booked": booked,
            "conflict": len(results) - booked, "results": results}


@app.get("/oa/bookings/my", tags=["OA"])
async def my_bookings(employee_id: str, date: Optional[str] = None):
    sql = "SELECT * FROM bookings WHERE employee_id=:emp AND status='booked'"
    params: dict[str, Any] = {"emp": employee_id}
    if date:
        sql += " AND date=:d"
        params["d"] = _parse_date(date).isoformat()
    sql += " ORDER BY date, start_time"
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    return {"employee_id": employee_id, "bookings": [_fmt_row(dict(r)) for r in rows]}


@app.get("/oa/bookings/{booking_id}", tags=["OA"])
async def get_booking(booking_id: str):
    async with engine.connect() as conn:
        row = await _get_booking(conn, booking_id)
    if not row:
        raise HTTPException(404, f"预订不存在：{booking_id}")
    return _fmt_row(row)


@app.get("/oa/rooms/{room_id}", tags=["OA"])
async def get_room(room_id: str):
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT * FROM rooms WHERE room_id=:rid AND active=1"),
            {"rid": room_id})).mappings().first()
    if not row:
        raise HTTPException(404, f"会议室不存在：{room_id}")
    return _fmt_row(dict(row))


@app.get("/oa/reports/room_usage", tags=["OA"])
async def room_usage_report(
    start_date: str = Query(..., alias="from"),
    end_date: str = Query(..., alias="to"),
    building: Optional[str] = None,
):
    """会议室利用率聚合（只读；V3 运营报表用）。"""
    f, t = _parse_date(start_date).isoformat(), _parse_date(end_date).isoformat()
    sql = """
        SELECT r.room_id, r.building, r.floor, r.name, r.capacity,
               COUNT(b.booking_id) AS booking_count,
               COALESCE(SUM(TIMESTAMPDIFF(MINUTE, b.start_time, b.end_time)) / 60, 0) AS booked_hours
        FROM rooms r
        LEFT JOIN bookings b
               ON b.room_id = r.room_id AND b.status='booked'
              AND b.date BETWEEN :f AND :t
        WHERE r.active=1 AND (:building IS NULL OR r.building=:building)
        GROUP BY r.room_id, r.building, r.floor, r.name, r.capacity
        ORDER BY booked_hours DESC, r.room_id
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"f": f, "t": t, "building": building})).mappings().all()
    return {"from": f, "to": t,
            "rooms": [{**dict(r), "booked_hours": float(r["booked_hours"] or 0)} for r in rows]}


# ── 资产：动态可用状态（V2 用，先留接口）───────────────────

@app.get("/asset/availability", tags=["资产"])
async def asset_availability(asset_ids: str):
    """按资产 ID 列表查动态可用状态（逗号分隔）。"""
    ids = [x.strip() for x in asset_ids.split(",") if x.strip()]
    if not ids:
        return []
    stmt = text("SELECT * FROM asset_availability WHERE asset_id IN :ids").bindparams(
        bindparam("ids", expanding=True))
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt, {"ids": ids})).mappings().all()
    return [_fmt_row(dict(r)) for r in rows]


@app.get("/asset/ledger", tags=["资产"])
async def asset_ledger():
    """资产系统台账全量接口（模拟外部系统的每日同步来源）。"""
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT asset_id, name, category, brand, model, department, owner_id, status, borrowable, updated_at "
            "FROM asset_ledger_source ORDER BY category, asset_id"))).mappings().all()
    return {"total": len(rows), "items": [_fmt_row(dict(r)) for r in rows]}


if __name__ == "__main__":
    import uvicorn
    port = 8210
    print(f"Mock 内部系统 → http://localhost:{port}  （文档见 /docs）")
    uvicorn.run(app, host="0.0.0.0", port=port)
