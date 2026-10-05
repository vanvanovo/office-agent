# backend/api/v1/reports.py
# 运营报表接口（仅 admin 角色）：会议室利用率 / 报修热点。
# 数据链路：网关 → MCP 工具（参数化）→ Mock OA / office_asset ——与对话链路同源，不复制业务数据。

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.config import get_settings
from backend.core.mcp_client import call_mcp_tool
from backend.dependencies import get_current_user

router = APIRouter()
settings = get_settings()


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "仅行政/IT 角色可查看运营报表")
    return user


@router.get("/reports/rooms")
async def rooms_report(start_date: str = Query(..., alias="from"),
                       end_date: str = Query(..., alias="to"),
                       building: str | None = None,
                       user: dict = Depends(require_admin)):
    args = {"start_date": start_date, "end_date": end_date}
    if building:
        args["building"] = building
    r = await call_mcp_tool(settings.oa_mcp_url, "get_room_usage_report", args)
    if r.get("status") != "success":
        return {"rooms": [], "error": r.get("message")}
    return r.get("data") or {"rooms": []}


@router.get("/reports/repairs")
async def repairs_report(start_date: str = Query(..., alias="from"),
                         end_date: str = Query(..., alias="to"),
                         user: dict = Depends(require_admin)):
    r = await call_mcp_tool(settings.asset_mcp_url, "get_repair_hotspots",
                            {"start_date": start_date, "end_date": end_date})
    if r.get("status") != "success":
        return {"by_category": [], "top_assets": [], "error": r.get("message")}
    return r.get("data") or {"by_category": [], "top_assets": []}
