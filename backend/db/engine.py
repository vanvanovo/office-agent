# backend/db/engine.py
# 数据库引擎与查询小工具：三个库（oa / asset / app）各一个惰性创建的异步引擎。
#
# 用法：
#   rows = await fetch_all("app", "SELECT ... WHERE x=:x", {"x": 1})
#   await execute("app", "INSERT INTO ... VALUES (:a)", {"a": ...})

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.config import get_settings

_engines: dict[str, AsyncEngine] = {}


def get_engine(name: str) -> AsyncEngine:
    if name not in _engines:
        s = get_settings()
        urls = {"oa": s.mysql_oa_url, "asset": s.mysql_asset_url, "app": s.mysql_app_url}
        if name not in urls:
            raise ValueError(f"未知数据库：{name}（可用：{list(urls)}）")
        _engines[name] = create_async_engine(urls[name], pool_size=5, max_overflow=10)
    return _engines[name]


async def fetch_all(db: str, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    async with get_engine(db).connect() as conn:
        rows = (await conn.execute(text(sql), params or {})).mappings().all()
    return [dict(r) for r in rows]


async def fetch_one(db: str, sql: str, params: dict[str, Any] | None = None) -> dict | None:
    rows = await fetch_all(db, sql, params)
    return rows[0] if rows else None


async def execute(db: str, sql: str, params: dict[str, Any] | None = None) -> None:
    async with get_engine(db).begin() as conn:
        await conn.execute(text(sql), params or {})
