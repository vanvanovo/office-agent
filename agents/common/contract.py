# agents/common/contract.py
# A2A Task 契约：请求解析（防御式）+ 响应封装（completed / input-required / failed）。
#
# 契约（方案 §6.1）：
#   请求 message.content.text = JSON 字符串：
#     {"action": "query|book|reschedule|cancel|my_bookings",
#      "query": "改写后的问题", "context": [...], "slots": {...}, "employee": {"id": "E1001", "role": "employee"}}
#   返回 artifacts[0].parts[0].text = JSON 字符串：
#     {"answer", "data", "sources", "need_clarify", "clarify"}
#   状态：completed / input-required / failed

from __future__ import annotations

import json
from typing import Any, Optional

from python_a2a import Task, TaskState, TaskStatus


class TaskRequest:
    """主控发来的 Task 请求（防御式解析：JSON 与纯文本都能吃）。"""

    def __init__(self, action: str = "", query: str = "",
                 context: Optional[list] = None, slots: Optional[dict] = None,
                 employee: Optional[dict] = None, task_id: str = ""):
        self.action = action
        self.query = query
        self.context = context or []
        self.slots = slots or {}
        self.employee = employee or {}
        self.task_id = task_id

    @property
    def employee_id(self) -> str:
        return str(self.employee.get("id", "") or "")

    @classmethod
    def parse(cls, task: Task) -> "TaskRequest":
        raw = ""
        try:
            raw = (((task.message or {}).get("content") or {}) or {}).get("text", "") or ""
        except Exception:  # noqa: BLE001
            raw = ""
        raw = str(raw).strip()
        task_id = str(getattr(task, "id", "") or "")
        if not raw:
            return cls(task_id=task_id)
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return cls(action=str(data.get("action") or ""),
                           query=str(data.get("query") or ""),
                           context=data.get("context") or [],
                           slots=data.get("slots") or {},
                           employee=data.get("employee") or {},
                           task_id=task_id)
        except (json.JSONDecodeError, TypeError):
            pass
        return cls(query=raw, task_id=task_id)


def complete(task: Task, *, answer: str, data: Optional[dict] = None,
             sources: Optional[list] = None) -> Task:
    payload = {"answer": answer, "data": data or {}, "sources": sources or [],
               "need_clarify": False, "clarify": ""}
    task.artifacts = [{"parts": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}]
    task.status = TaskStatus(state=TaskState.COMPLETED)
    return task


def input_required(task: Task, *, clarify: str, answer: str = "",
                   data: Optional[dict] = None) -> Task:
    payload = {"answer": answer, "data": data or {}, "sources": [],
               "need_clarify": True, "clarify": clarify}
    task.artifacts = [{"parts": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}]
    # 注意：追问文案必须写进 status.message —— 客户端读的是 status.message；
    # 只写 task.message 不会被序列化传出（踩坑记录：P3 首跑追问文案为空）。
    task.status = TaskStatus(state=TaskState.INPUT_REQUIRED,
                             message={"role": "agent", "content": {"text": clarify}})
    return task


def failed(task: Task, *, message: str) -> Task:
    task.status = TaskStatus(state=TaskState.FAILED,
                             message={"role": "agent", "content": {"text": message}})
    return task


def render_result(task: Task, result: dict[str, Any]) -> Task:
    """把 _handle 的统一返回（dict with state）渲染成 Task。"""
    state = result.get("state", "completed")
    if state == "completed":
        return complete(task, answer=result.get("answer", ""), data=result.get("data"),
                        sources=result.get("sources"))
    if state == "input_required":
        return input_required(task, clarify=result.get("clarify", ""),
                              answer=result.get("answer", ""), data=result.get("data"))
    return failed(task, message=result.get("message", "处理失败"))
