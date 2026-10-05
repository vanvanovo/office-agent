# mcp_servers/oa_server.py
# OA MCP 工具服务（:8111）：把 Mock 内部系统的会议室接口封装成「参数化 MCP 工具」。
#
# 安全红线：
#   - 不暴露任何 sql 参数：模型只负责「选工具 + 填参数」，SQL 由工具内部固定；
#   - 服务间调用带 X-MCP-Key 鉴权；
#   - 写操作幂等键 + 审计落库（office_app.write_audit）。
#
# 运行：python mcp_servers/oa_server.py   → http://localhost:8111/mcp

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402
from mcp_servers.auth import ApiKeyAuthMiddleware  # noqa: E402

logger = get_logger(__name__)
settings = get_settings()

MCP_NAME = "OATools"
PORT = 8111

mcp = FastMCP(
    name=MCP_NAME,
    instructions="会议室工具：查询可用会议室、预订、改期、取消、我的预订。全部参数化，不支持 SQL。",
    host="127.0.0.1",
    port=PORT,
    stateless_http=True,
    json_response=True,
    log_level="WARNING",
)

_http = httpx.AsyncClient(
    base_url=settings.mock_internal_url,
    timeout=settings.mcp_timeout_seconds,   # MCP 调用超时 5s
    trust_env=False,
)
_audit_engine = create_async_engine(settings.mysql_app_url)


# ── 工具函数 ──────────────────────────────────────────────

def _ok(data: Any) -> str:
    return json.dumps({"status": "success", "data": data}, ensure_ascii=False, default=str)


def _err(status: str, message: str, **extra) -> str:
    return json.dumps({"status": status, "message": message, **extra},
                      ensure_ascii=False, default=str)


async def _call(method: str, url: str, **kw) -> tuple[int, Any]:
    """调用 Mock 内部系统；网络/超时异常统一转成 (状态码, 载荷)。"""
    try:
        resp = await _http.request(method, url, **kw)
    except httpx.TimeoutException:
        logger.warning("oa_mcp.upstream_timeout", method=method, url=url,
                       timeout=settings.mcp_timeout_seconds)
        return 504, {"detail": f"OA 系统超时（>{settings.mcp_timeout_seconds}s）"}
    except Exception as e:  # noqa: BLE001
        logger.error("oa_mcp.upstream_error", method=method, url=url, error=str(e)[:200])
        return 502, {"detail": f"OA 系统连接失败：{e}"}
    try:
        return resp.status_code, resp.json()
    except Exception:  # noqa: BLE001
        return resp.status_code, {"detail": resp.text}


async def _audit(action: str, target: str, actor: str, result: str,
                 idem: str | None = None, detail: dict | None = None) -> None:
    """写操作审计（失败不影响主流程，只告警）。"""
    try:
        async with _audit_engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO write_audit (action, target, actor, idempotency_key, result, detail)
                VALUES (:action, :target, :actor, :idem, :result, :detail)
            """), {"action": action, "target": target, "actor": actor,
                   "idem": idem, "result": result,
                   "detail": json.dumps(detail or {}, ensure_ascii=False, default=str)})
    except Exception as e:  # noqa: BLE001
        logger.warning("oa_mcp.audit_failed", action=action, error=str(e)[:200])


# ── 读工具 ────────────────────────────────────────────────

@mcp.tool()
async def list_available_rooms(date: str, start_time: str, end_time: str,
                               building: str | None = None, floor: int | None = None,
                               capacity_min: int | None = None) -> str:
    """查询某天某时段内可用的会议室（实时数据，来自 OA 系统，不落库）。

    Args:
        date: 日期，格式 YYYY-MM-DD，例如 2026-10-06。
        start_time: 开始时间，格式 HH:MM（24 小时制），例如 15:00。
        end_time: 结束时间，格式 HH:MM，例如 16:00。
        building: 可选，楼栋筛选，例如 A 或 B。
        floor: 可选，楼层筛选，例如 8。
        capacity_min: 可选，最小容纳人数，例如 10。
    """
    status, data = await _call("GET", "/oa/availability", params={
        k: v for k, v in {
            "date": date, "start_time": start_time, "end_time": end_time,
            "building": building, "floor": floor, "capacity_min": capacity_min,
        }.items() if v is not None
    })
    if status == 200:
        return _ok(data)
    return _err("error", str(data.get("detail", "查询失败")))


@mcp.tool()
async def get_room_info(room_id: str) -> str:
    """查询单间会议室的详细信息（名称 / 楼层 / 容量 / 设备）。

    Args:
        room_id: 会议室编号，例如 A-801。
    """
    status, data = await _call("GET", f"/oa/rooms/{room_id}")
    if status == 200:
        return _ok(data)
    if status == 404:
        return _err("not_found", f"会议室不存在：{room_id}")
    return _err("error", str(data.get("detail", "查询失败")))


@mcp.tool()
async def list_my_bookings(employee_id: str, date: str | None = None) -> str:
    """查询某员工的预订列表（status=booked，按时间排序）。

    Args:
        employee_id: 员工工号，例如 E1001。
        date: 可选，只看某天，格式 YYYY-MM-DD；不传则查全部。
    """
    params: dict[str, Any] = {"employee_id": employee_id}
    if date:
        params["date"] = date
    status, data = await _call("GET", "/oa/bookings/my", params=params)
    if status == 200:
        return _ok(data)
    return _err("error", str(data.get("detail", "查询失败")))


@mcp.tool()
async def get_room_usage_report(start_date: str, end_date: str,
                                building: str | None = None) -> str:
    """会议室利用率报表（只读聚合：每间房的预订次数与占用小时数）。仅行政/IT 使用。

    Args:
        start_date: 起始日期 YYYY-MM-DD。
        end_date: 结束日期 YYYY-MM-DD。
        building: 可选，楼栋筛选（A / B）。
    """
    params: dict[str, Any] = {"from": start_date, "to": end_date}
    if building:
        params["building"] = building
    status, data = await _call("GET", "/oa/reports/room_usage", params=params)
    if status == 200:
        return _ok(data)
    return _err("error", str(data.get("detail", "报表查询失败")))


# ── 写工具（幂等 + 审计）──────────────────────────────────

@mcp.tool()
async def book_room(room_id: str, date: str, start_time: str, end_time: str,
                    employee_id: str, purpose: str | None = None,
                    idempotency_key: str | None = None) -> str:
    """预订会议室。写操作带幂等键；时段冲突时返回 conflict，应让用户改选时段。

    Args:
        room_id: 会议室编号，例如 A-801；必须先确认该时段可用。
        date: 日期 YYYY-MM-DD。
        start_time: 开始时间 HH:MM。
        end_time: 结束时间 HH:MM。
        employee_id: 预订人工号（来自当前登录员工，不要编造）。
        purpose: 可选，会议用途。
        idempotency_key: 可选，幂等键；重试时必须复用同一个键，避免重复下单。
    """
    payload = {"room_id": room_id, "date": date, "start_time": start_time,
               "end_time": end_time, "employee_id": employee_id,
               "purpose": purpose, "idempotency_key": idempotency_key}
    status, data = await _call("POST", "/oa/bookings", json=payload)
    if status in (200, 201):
        await _audit("book_room", room_id, employee_id, "ok", idempotency_key,
                     {"request": payload, "response": data})
        return _ok(data)
    if status == 409:
        await _audit("book_room", room_id, employee_id, "conflict", idempotency_key,
                     {"request": payload, "response": data})
        return _err("conflict", str(data.get("detail", "该时段已被预订")))
    if status == 403:
        return _err("rejected", str(data.get("detail", "无权限操作")))
    await _audit("book_room", room_id, employee_id, "error", idempotency_key,
                 {"request": payload, "response": data})
    return _err("error", str(data.get("detail", "预订失败")))


@mcp.tool()
async def reschedule_booking(booking_id: str, new_date: str, new_start_time: str,
                             new_end_time: str, employee_id: str,
                             idempotency_key: str | None = None) -> str:
    """会议室改期（先定新时段、成功后再释放原时段）。

    Args:
        booking_id: 原预订编号，例如 BK20261006-XXXXXX。
        new_date: 新日期 YYYY-MM-DD。
        new_start_time: 新开始时间 HH:MM。
        new_end_time: 新结束时间 HH:MM。
        employee_id: 操作人工号（只能改自己的预订）。
        idempotency_key: 可选，幂等键。
    """
    payload = {"new_date": new_date, "new_start_time": new_start_time,
               "new_end_time": new_end_time, "employee_id": employee_id,
               "idempotency_key": idempotency_key}
    status, data = await _call("POST", f"/oa/bookings/{booking_id}/reschedule", json=payload)
    if status == 200:
        await _audit("reschedule_booking", booking_id, employee_id, "ok", idempotency_key,
                     {"request": payload, "response": data})
        return _ok(data)
    if status == 409:
        await _audit("reschedule_booking", booking_id, employee_id, "conflict", idempotency_key,
                     {"request": payload, "response": data})
        return _err("conflict", str(data.get("detail", "目标时段冲突")))
    if status == 403:
        return _err("rejected", str(data.get("detail", "只能操作自己的预订")))
    if status == 404:
        return _err("not_found", str(data.get("detail", "预订不存在")))
    await _audit("reschedule_booking", booking_id, employee_id, "error", idempotency_key,
                 {"request": payload, "response": data})
    return _err("error", str(data.get("detail", "改期失败")))


@mcp.tool()
async def book_recurring(room_id: str, weekday: int, start_time: str, end_time: str,
                         until: str, employee_id: str,
                         purpose: str | None = None,
                         idempotency_key: str | None = None) -> str:
    """周期预订：每周固定时段订到某天（如"每周一 10 点订 8 周"）。冲突场次不落单、逐条返回清单。

    Args:
        room_id: 会议室编号，例如 B-601。
        weekday: 星期几：1=周一，2=周二，…，7=周日。
        start_time: 开始时间 HH:MM。
        end_time: 结束时间 HH:MM。
        until: 截止日期 YYYY-MM-DD（含当天）。
        employee_id: 预订人工号。
        purpose: 可选，会议用途。
        idempotency_key: 可选，幂等键（每次会按日期派生）。
    """
    payload = {"room_id": room_id, "weekday": weekday, "start_time": start_time,
               "end_time": end_time, "until": until, "employee_id": employee_id,
               "purpose": purpose, "idempotency_key": idempotency_key}
    status, data = await _call("POST", "/oa/bookings/recurring", json=payload)
    if status in (200, 201):
        summary = {k: v for k, v in data.items() if k != "results"}
        await _audit("book_recurring", room_id, employee_id, "ok", idempotency_key,
                     {"request": payload, "response": summary})
        return _ok(data)
    if status == 400:
        return _err("invalid", str(data.get("detail", "周期参数不合法")))
    if status == 404:
        return _err("not_found", str(data.get("detail", "会议室不存在")))
    await _audit("book_recurring", room_id, employee_id, "error", idempotency_key,
                 {"request": payload, "response": data})
    return _err("error", str(data.get("detail", "周期预订失败")))


@mcp.tool()
async def cancel_booking(booking_id: str, employee_id: str) -> str:
    """取消会议室预订（只能取消自己的；取消后时段自动释放）。

    Args:
        booking_id: 预订编号，例如 BK20261006-XXXXXX。
        employee_id: 操作人工号。
    """
    status, data = await _call("POST", f"/oa/bookings/{booking_id}/cancel",
                               json={"employee_id": employee_id})
    if status == 200:
        await _audit("cancel_booking", booking_id, employee_id, "ok", None, {"response": data})
        return _ok(data)
    if status == 403:
        await _audit("cancel_booking", booking_id, employee_id, "rejected", None, {"response": data})
        return _err("rejected", str(data.get("detail", "只能取消自己的预订")))
    if status == 404:
        return _err("not_found", str(data.get("detail", "预订不存在")))
    await _audit("cancel_booking", booking_id, employee_id, "error", None, {"response": data})
    return _err("error", str(data.get("detail", "取消失败")))


if __name__ == "__main__":
    import uvicorn

    configure_logging()
    app = mcp.streamable_http_app()
    app = ApiKeyAuthMiddleware(app, settings.mcp_shared_key)
    key_state = "已启用" if settings.mcp_shared_key else "未配置（放行）"
    print(f"{MCP_NAME} MCP → http://localhost:{PORT}/mcp  | X-MCP-Key {key_state}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
