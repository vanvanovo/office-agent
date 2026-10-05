# mcp_servers/asset_server.py
# 器材 MCP 工具服务（:8112）
#   - 静态属性（名称/型号/部门/状态）：office_asset.equipment（每日同步产物）
#   - 动态状态（可借/持有人/归还时间）：Mock 内部系统 /asset/availability（实时）
#   - 报修工单：参数化 INSERT + 幂等键 + SLA + 审计；催单带归属校验
#   - SQL 白名单：只允许按固定字段过滤（category/keyword/department/status），不暴露 SQL
#
# 运行：python mcp_servers/asset_server.py   → http://localhost:8112/mcp

from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, timedelta
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

MCP_NAME = "AssetTools"
PORT = 8112
SLA_HOURS = {"high": 4, "medium": 24, "low": 72}

mcp = FastMCP(
    name=MCP_NAME,
    instructions="器材工具：查询台账（静态+实时可借状态）、报修、查进度、催单。全部参数化，不支持 SQL。",
    host="127.0.0.1",
    port=PORT,
    stateless_http=True,
    json_response=True,
    log_level="WARNING",
)

_http = httpx.AsyncClient(base_url=settings.mock_internal_url,
                          timeout=settings.mcp_timeout_seconds, trust_env=False)
_engine = create_async_engine(settings.mysql_asset_url)
_audit_engine = create_async_engine(settings.mysql_app_url)


# ── 工具函数 ──────────────────────────────────────────────

def _ok(data: Any) -> str:
    return json.dumps({"status": "success", "data": data}, ensure_ascii=False, default=str)


def _err(status: str, message: str, **extra) -> str:
    return json.dumps({"status": status, "message": message, **extra}, ensure_ascii=False, default=str)


def _fmt_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat(sep=" ", timespec="minutes")
        elif k == "attachments" and isinstance(v, str) and v:
            try:
                out[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                out[k] = v
        else:
            out[k] = v
    return out


async def _audit(action: str, target: str, actor: str, result: str,
                 idem: str | None = None, detail: dict | None = None) -> None:
    try:
        async with _audit_engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO write_audit (action, target, actor, idempotency_key, result, detail)
                VALUES (:action, :target, :actor, :idem, :result, :detail)
            """), {"action": action, "target": target, "actor": actor, "idem": idem,
                   "result": result, "detail": json.dumps(detail or {}, ensure_ascii=False, default=str)})
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_mcp.audit_failed", action=action, error=str(e)[:200])


async def _dynamic_states(asset_ids: list[str]) -> dict[str, dict]:
    """从资产系统实时拉动态状态（可借/持有人/归还时间）。失败时降级为空（只用静态兜底）。"""
    if not asset_ids:
        return {}
    try:
        resp = await _http.get("/asset/availability", params={"asset_ids": ",".join(asset_ids)})
        resp.raise_for_status()
        return {x["asset_id"]: x for x in resp.json()}
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_mcp.dynamic_unavailable", error=str(e)[:150])
        return {}


async def _merge_dynamic(items: list[dict]) -> list[dict]:
    dyn = await _dynamic_states([i["asset_id"] for i in items])
    for i in items:
        d = dyn.get(i["asset_id"], {})
        i["borrowable_now"] = bool(d.get("borrowable_now", i.get("borrowable", 0)))
        i["holder"] = d.get("holder")
        i["due_back"] = d.get("due_back")
    return items


# ── 工具 ──────────────────────────────────────────────────

@mcp.tool()
async def query_equipment(category: str | None = None, keyword: str | None = None,
                          department: str | None = None, status: str | None = None,
                          only_borrowable: bool = False) -> str:
    """查询器材台账：静态属性来自台账库，可借状态实时来自资产系统。

    Args:
        category: 可选，类别精确匹配，例如 投影仪 / 笔记本电脑 / VR头显 / 显示器 / 相机 / 平板 / 打印机。
        keyword: 可选，关键词模糊匹配（名称/品牌/型号/资产编号），例如 投影 或 P001。
        department: 可选，归属部门，例如 设计部 / 研发部 / 市场部 / 行政部。
        status: 可选，台账状态：in_stock（在库）/ issued_to_staff（已领用）。
        only_borrowable: 可选，只看当前可借的（默认 false）。
    """
    sql = ("SELECT asset_id, name, category, brand, model, department, owner_id, status, borrowable "
           "FROM equipment WHERE 1=1")
    params: dict[str, Any] = {}
    if category:
        sql += " AND category=:category"
        params["category"] = category
    if keyword:
        sql += " AND (name LIKE :kw OR brand LIKE :kw OR model LIKE :kw OR asset_id LIKE :kw)"
        params["kw"] = f"%{keyword}%"
    if department:
        sql += " AND department=:department"
        params["department"] = department
    if status:
        sql += " AND status=:status"
        params["status"] = status
    sql += " ORDER BY category, asset_id LIMIT 60"

    async with _engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    items = await _merge_dynamic([dict(r) for r in rows])
    if only_borrowable:
        items = [i for i in items if i["borrowable_now"]]
    return _ok({"total": len(items), "items": items})


@mcp.tool()
async def get_equipment(asset_id: str) -> str:
    """查询单件器材的详情（静态属性 + 实时可借状态）。

    Args:
        asset_id: 资产编号，例如 P001。
    """
    async with _engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT asset_id, name, category, brand, model, department, owner_id, status, borrowable "
            "FROM equipment WHERE asset_id=:a"), {"a": asset_id})).mappings().first()
    if not row:
        return _err("not_found", f"器材不存在：{asset_id}")
    item = (await _merge_dynamic([dict(row)]))[0]
    return _ok(item)


@mcp.tool()
async def create_repair_ticket(fault_desc: str, asset_id: str | None = None,
                               urgency: str = "medium", reporter_id: str = "",
                               attachments: str | None = None,
                               idempotency_key: str | None = None) -> str:
    """创建报修工单（参数化写入 + 幂等键 + SLA）。

    Args:
        fault_desc: 故障现象描述，例如 屏幕碎裂 / 无法开机 / 画面偏色。
        asset_id: 可选，资产编号；不确定时先用 query_equipment 查。
        urgency: 可选，紧急度：high（4 小时）/ medium（24 小时）/ low（72 小时），默认 medium。
        reporter_id: 报修人工号（来自当前登录员工，不要编造）。
        attachments: 可选，附件 JSON 数组字符串（图片地址），例如 ["data/uploads/a.jpg"]。
        idempotency_key: 可选，幂等键；重试必须复用同一个键。
    """
    urgency = urgency if urgency in SLA_HOURS else "medium"
    sla_due = datetime.now() + timedelta(hours=SLA_HOURS[urgency])
    ticket_no = f"RT{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

    try:
        async with _engine.begin() as conn:
            if idempotency_key:
                r = await conn.execute(text("SELECT * FROM repair_tickets WHERE idempotency_key=:k"),
                                       {"k": idempotency_key})
                row = r.mappings().first()
                if row:
                    logger.info("asset_mcp.repair_idempotent_hit", key=idempotency_key)
                    return _ok({"idempotent": True, "ticket": _fmt_row(dict(row))})

            await conn.execute(text("""
                INSERT INTO repair_tickets
                    (ticket_no, asset_id, fault_desc, urgency, reporter_id, status,
                     attachments, idempotency_key, sla_due_at)
                VALUES (:t, :a, :f, :u, :r, 'open', :att, :k, :sla)
            """), {"t": ticket_no, "a": asset_id, "f": fault_desc, "u": urgency,
                   "r": reporter_id, "att": attachments, "k": idempotency_key, "sla": sla_due})
            row = (await conn.execute(text("SELECT * FROM repair_tickets WHERE ticket_no=:t"),
                                      {"t": ticket_no})).mappings().first()
    except Exception as e:  # noqa: BLE001
        logger.error("asset_mcp.repair_failed", error=str(e)[:200])
        return _err("error", f"创建工单失败：{e}")

    await _audit("create_repair_ticket", ticket_no, reporter_id, "ok", idempotency_key,
                 {"asset_id": asset_id, "urgency": urgency, "fault_desc": fault_desc})
    logger.info("asset_mcp.repair_created", ticket_no=ticket_no, asset=asset_id,
                urgency=urgency, reporter=reporter_id)
    return _ok({"ticket": _fmt_row(dict(row))})


@mcp.tool()
async def query_repair_ticket(ticket_id: str) -> str:
    """查询单个报修工单的进度。

    Args:
        ticket_id: 工单号，例如 RT20261006-AB12。
    """
    async with _engine.connect() as conn:
        row = (await conn.execute(text("SELECT * FROM repair_tickets WHERE ticket_no=:t"),
                                  {"t": ticket_id})).mappings().first()
    if not row:
        return _err("not_found", f"工单不存在：{ticket_id}")
    return _ok({"ticket": _fmt_row(dict(row))})


@mcp.tool()
async def list_my_tickets(reporter_id: str) -> str:
    """查询某员工提交的全部报修工单（按时间倒序）。

    Args:
        reporter_id: 员工工号，例如 E1001。
    """
    async with _engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT * FROM repair_tickets WHERE reporter_id=:r ORDER BY created_at DESC LIMIT 30"),
            {"r": reporter_id})).mappings().all()
    return _ok({"total": len(rows), "tickets": [_fmt_row(dict(r)) for r in rows]})


@mcp.tool()
async def urge_repair_ticket(ticket_id: str, reporter_id: str) -> str:
    """催单：仅本人可催（写审计；V3 起会同时给 IT 发站内信）。

    Args:
        ticket_id: 工单号。
        reporter_id: 操作人工号（只能催自己的工单）。
    """
    async with _engine.begin() as conn:
        row = (await conn.execute(text("SELECT * FROM repair_tickets WHERE ticket_no=:t"),
                                  {"t": ticket_id})).mappings().first()
        if not row:
            return _err("not_found", f"工单不存在：{ticket_id}")
        if row["reporter_id"] != reporter_id:
            await _audit("urge_repair_ticket", ticket_id, reporter_id, "rejected",
                         detail={"reason": "not_owner"})
            return _err("rejected", "只能催自己提交的工单")
        if row["status"] in ("done", "closed"):
            return _ok({"ticket_id": ticket_id, "status": row["status"],
                        "message": "工单已完成，无需催单"})
        await conn.execute(text("UPDATE repair_tickets SET urged_at=NOW() WHERE ticket_no=:t"),
                           {"t": ticket_id})
        row = (await conn.execute(text("SELECT * FROM repair_tickets WHERE ticket_no=:t"),
                                  {"t": ticket_id})).mappings().first()

    await _audit("urge_repair_ticket", ticket_id, reporter_id, "ok")
    logger.info("asset_mcp.repair_urged", ticket_no=ticket_id, reporter=reporter_id)
    return _ok({"ticket": _fmt_row(dict(row))})


@mcp.tool()
async def get_repair_hotspots(start_date: str, end_date: str) -> str:
    """报修热点报表（只读聚合：按器材类别统计 + 高频报修资产 Top5）。仅行政/IT 使用。

    Args:
        start_date: 起始日期 YYYY-MM-DD。
        end_date: 结束日期 YYYY-MM-DD。
    """
    by_cat_sql = """
        SELECT COALESCE(e.category, '未分类') AS category, COUNT(*) AS ticket_count
        FROM repair_tickets t
        LEFT JOIN equipment e ON e.asset_id = t.asset_id
        WHERE t.created_at >= :f AND t.created_at < DATE_ADD(:t, INTERVAL 1 DAY)
        GROUP BY category
        ORDER BY ticket_count DESC, category
    """
    top_asset_sql = """
        SELECT t.asset_id, COALESCE(e.name, '—') AS name, COUNT(*) AS ticket_count
        FROM repair_tickets t
        LEFT JOIN equipment e ON e.asset_id = t.asset_id
        WHERE t.created_at >= :f AND t.created_at < DATE_ADD(:t, INTERVAL 1 DAY)
          AND t.asset_id IS NOT NULL
        GROUP BY t.asset_id, name
        ORDER BY ticket_count DESC, t.asset_id
        LIMIT 5
    """
    params = {"f": start_date, "t": end_date}
    async with _engine.connect() as conn:
        by_cat = (await conn.execute(text(by_cat_sql), params)).mappings().all()
        top_assets = (await conn.execute(text(top_asset_sql), params)).mappings().all()
    return _ok({"by_category": [dict(r) for r in by_cat],
                "top_assets": [dict(r) for r in top_assets]})


if __name__ == "__main__":
    import uvicorn

    configure_logging()
    app = mcp.streamable_http_app()
    app = ApiKeyAuthMiddleware(app, settings.mcp_shared_key)
    key_state = "已启用" if settings.mcp_shared_key else "未配置（放行）"
    print(f"{MCP_NAME} MCP → http://localhost:{PORT}/mcp  | X-MCP-Key {key_state}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
