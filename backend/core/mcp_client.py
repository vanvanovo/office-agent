# backend/core/mcp_client.py
# 网关侧的通用 MCP 客户端（JSON-RPC 调 stateless MCP Server）。
# 与 agents/common/mcp_client.py 同协议；放两份是为了让「网关」与「Agent 进程」各自独立装配。

import json
from typing import Any

import httpx

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_settings.mcp_timeout_seconds, trust_env=False)
    return _client


async def call_mcp_tool(server_url: str, tool_name: str, arguments: dict[str, Any]) -> dict:
    """调用 MCP 工具；成功返回 {"status": "success", "data": ...}，失败返回 {"status": "error", ...}。"""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if _settings.mcp_shared_key:
        headers["X-MCP-Key"] = _settings.mcp_shared_key

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool_name, "arguments": arguments}}
    try:
        resp = await _get_client().post(server_url, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning("gateway.mcp_timeout", tool=tool_name)
        return {"status": "error", "message": f"MCP 超时（>{_settings.mcp_timeout_seconds}s）"}
    except Exception as e:  # noqa: BLE001
        logger.error("gateway.mcp_error", tool=tool_name, error=str(e)[:200])
        return {"status": "error", "message": f"MCP 连接失败：{e}"}

    if resp.status_code != 200:
        return {"status": "error", "message": f"MCP HTTP {resp.status_code}"}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {"status": "error", "message": "MCP 返回非 JSON"}
    if "error" in data:
        return {"status": "error", "message": str(data["error"])}
    for item in data.get("result", {}).get("content", []):
        text = item.get("text", "") if isinstance(item, dict) else ""
        if text:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"status": "success", "data": {"raw": text}}
    return {"status": "error", "message": "MCP 返回为空"}
