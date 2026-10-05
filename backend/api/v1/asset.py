# backend/api/v1/asset.py
# 器材台账 REST（管理视图用；数据链路与对话一致：走器材查询 Agent）

from fastapi import APIRouter, Depends

from backend.config import get_settings
from backend.core.a2a_client import call_agent
from backend.dependencies import get_current_user

router = APIRouter()
settings = get_settings()


@router.get("/equipment")
async def list_equipment(category: str | None = None, keyword: str | None = None,
                         department: str | None = None,
                         user: dict = Depends(get_current_user)):
    slots = {k: v for k, v in
             {"category": category, "keyword": keyword, "department": department}.items() if v}
    r = await call_agent(settings.equipment_query_agent_url, "query", "查询器材台账",
                         slots=slots, employee={"id": user["user_id"], "role": user["role"]})
    items = ((r.get("result") or {}).get("data") or {}).get("equipment", []) \
        if r.get("state") == "completed" else []
    return {"equipment": items}
