# backend/dependencies.py
# JWT 认证依赖：签发 / 校验访问令牌，返回当前员工身份。
#
# 演示形态（方案已定）：登录页一键选工号 → 后端签发 JWT → 业务层只消费身份（归属校验 / 审计）。
# 生产升级路径：接企业 SSO/OIDC，由 OA 门户带入身份（见方案《生产化边界》）。

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.config import get_settings

settings = get_settings()

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(emp_id: str, role: str, name: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {"sub": emp_id, "role": role, "name": name, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """解析 Bearer Token，返回 {"user_id", "role", "name"}；无效则 401。"""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials,
                             settings.jwt_secret_key,
                             algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise unauthorized from e

    emp_id = payload.get("sub")
    if not emp_id:
        raise unauthorized
    return {"user_id": emp_id, "role": payload.get("role", "employee"),
            "name": payload.get("name", "")}
