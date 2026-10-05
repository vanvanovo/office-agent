# backend/core/session.py
# 会话状态（进程内）：历史消息 + 追问轮次。
#
# 演示级实现：重启进程会话丢失；生产升级路径 = 落库/Redis（见方案《生产化边界》）。

import time
from dataclasses import dataclass, field

MAX_HISTORY = 20          # 最多保留 10 轮（user+assistant）
MAX_CLARIFY_ROUNDS = 2    # 追问上限：2 轮，第 3 次进引导


@dataclass
class Session:
    session_id: str
    emp_id: str
    role: str = "employee"
    name: str = ""
    history: list[dict] = field(default_factory=list)
    clarify_rounds: int = 0
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)


_sessions: dict[str, Session] = {}


def get_session(session_id: str, emp_id: str, role: str = "employee", name: str = "") -> Session:
    s = _sessions.get(session_id)
    if s is None:
        s = Session(session_id=session_id, emp_id=emp_id, role=role, name=name)
        _sessions[session_id] = s
    else:                                 # 身份以最新登录为准（演示时切换工号）
        s.emp_id, s.role, s.name = emp_id, role, name
    s.last_active = time.time()
    return s


def append_history(session: Session, role: str, content: str) -> None:
    session.history.append({"role": role, "content": content})
    if len(session.history) > MAX_HISTORY:
        session.history = session.history[-MAX_HISTORY:]


def add_clarify_round(session: Session) -> int:
    session.clarify_rounds += 1
    return session.clarify_rounds


def reset_clarify(session: Session) -> None:
    session.clarify_rounds = 0
