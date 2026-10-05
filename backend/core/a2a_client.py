# backend/core/a2a_client.py
# A2A 客户端：主控把「上下文 + 改写问题 + 槽位 + 员工信息」封装成 Task 发给业务 Agent。
#
# 设计要点：
#   - 统一超时（默认读配置 A2A_TIMEOUT_SECONDS），失败抛 A2ACallError 由调用方降级；
#   - 解析 Task 状态：completed → 读 artifacts；input-required → 追问；failed → 报错；
#   - 与各 Agent 的 artifacts 契约保持一致（JSON 字符串）。

import asyncio
import json
import uuid
from typing import Any, Optional

from python_a2a import A2AClient, Message, MessageRole, Task, TextContent

from backend.config import get_settings
from backend.core.exceptions import A2ACallError
from backend.core.logger import get_logger

logger = get_logger(__name__)


def _normalize_state(state: Any) -> str:
    """把 TaskState 枚举 / 字符串统一成 'completed' / 'input_required' / 'failed' 形状。"""
    val = getattr(state, "value", state)
    if val is None:
        return "unknown"
    return str(val).replace("-", "_").lower()


async def call_agent(
    agent_url: str,
    action: str,
    query: str,
    *,
    context: Optional[list] = None,
    slots: Optional[dict] = None,
    employee: Optional[dict] = None,
    timeout: Optional[float] = None,
) -> dict:
    """调用 A2A 业务 Agent，返回统一结构：

        {"state": "completed" | "input_required" | "failed",
         "result": dict | None,     # completed 时：Agent artifacts 里的结构化结果
         "message": str}            # input_required / failed 时的追问或错误文案

    失败（超时/连接）抛 A2ACallError，由调用方决定降级策略。
    """
    settings = get_settings()
    effective_timeout = timeout or settings.a2a_timeout_seconds

    payload = {
        "action": action,
        "query": query,
        "context": context or [],
        "slots": slots or {},
        "employee": employee or {},
    }
    message = Message(content=TextContent(text=json.dumps(payload, ensure_ascii=False)),
                      role=MessageRole.USER)
    task = Task(id=f"task-{uuid.uuid4().hex[:10]}", message=message.to_dict())

    client = A2AClient(agent_url)
    try:
        raw = await asyncio.wait_for(client.send_task_async(task), timeout=effective_timeout)
    except asyncio.TimeoutError as e:
        raise A2ACallError(f"A2A 超时（>{effective_timeout}s）：{agent_url}", agent_type="a2a") from e
    except Exception as e:  # noqa: BLE001
        raise A2ACallError(f"A2A 调用失败：{agent_url} | {e}", agent_type="a2a") from e

    state = _normalize_state(getattr(getattr(raw, "status", None), "state", None))

    # 解析 artifacts（JSON 优先，纯文本兜底）
    result: Optional[dict] = None
    try:
        text = raw.artifacts[0]["parts"][0]["text"]
        try:
            result = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            result = {"answer": text}
    except Exception:  # noqa: BLE001 —— 没有 artifacts 不算致命
        result = None

    # 解析 status.message（追问/错误文案）
    message_text = ""
    try:
        message_text = (((raw.status.message or {}).get("content") or {}).get("text") or "")
    except Exception:  # noqa: BLE001
        message_text = ""

    if state == "completed":
        return {"state": "completed", "result": result, "message": message_text}
    if state == "input_required":
        return {"state": "input_required", "result": result, "message": message_text}
    return {"state": "failed", "result": result,
            "message": message_text or f"Agent 任务未完成（state={state}）"}
