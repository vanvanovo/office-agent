# backend/api/v1/auth.py
# 认证接口：一键选工号登录（JWT）——演示形态；生产升级路径 = 企业 SSO。
#
#   GET  /api/v1/auth/employees  列出可登录的员工（登录页用）
#   POST /api/v1/auth/login      {emp_id} → JWT

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.db.engine import fetch_all, fetch_one
from backend.dependencies import create_access_token

logger = get_logger(__name__)
router = APIRouter()


class LoginRequest(BaseModel):
    emp_id: str


@router.get("/auth/employees")
async def list_employees():
    """登录页一键身份切换用：返回员工列表（演示环境）。"""
    rows = await fetch_all(
        "app",
        "SELECT emp_id, name, role, department FROM employees ORDER BY role DESC, emp_id")
    return {"employees": rows}


@router.post("/auth/login")
async def login(req: LoginRequest):
    emp_id = (req.emp_id or "").strip()
    row = await fetch_one(
        "app",
        "SELECT emp_id, name, role, department FROM employees WHERE emp_id=:e",
        {"e": emp_id})
    if not row:
        raise HTTPException(404, f"工号不存在：{emp_id}")
    token = create_access_token(row["emp_id"], row["role"], row["name"])
    logger.info("auth.login", emp_id=row["emp_id"], role=row["role"])
    return {"access_token": token, "token_type": "bearer", "emp": row}
