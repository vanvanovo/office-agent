# agents/common/mcp_client.py
# MCP 客户端：以 JSON-RPC 调 OA MCP 工具（服务端为 stateless_http + json_response）。
#
# 约定：网络/协议错误不抛异常，统一返回 {"status": "error", "message": ...}，
#       由 Agent 决定降级策略；成功返回 {"status": "success", "data": ...}。

import json
from typing import Any

import httpx

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """惰性创建共享 AsyncClient（在常驻事件循环内首次使用时创建，避免跨循环问题）。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_settings.mcp_timeout_seconds, trust_env=False)
    return _client


async def call_mcp_tool(tool_name: str, arguments: dict[str, Any],
                        server_url: str | None = None) -> dict:
    """调用 MCP 工具。server_url 不传时默认 OA MCP（会议室工具）。"""
    url = server_url or _settings.oa_mcp_url
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if _settings.mcp_shared_key:
        headers["X-MCP-Key"] = _settings.mcp_shared_key

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        resp = await _get_client().post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning("agent.mcp_timeout", tool=tool_name, timeout=_settings.mcp_timeout_seconds)
        return {"status": "error", "message": f"MCP 调用超时（>{_settings.mcp_timeout_seconds}s）"}
    except Exception as e:  # noqa: BLE001
        logger.error("agent.mcp_connect_error", tool=tool_name, error=str(e)[:200])
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
