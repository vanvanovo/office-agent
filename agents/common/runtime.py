# agents/common/runtime.py
# A2A Agent 运行时：常驻事件循环。
#
# 为什么需要：python-a2a 的 handle_task 是同步的，而 MCP 调用是异步的。
# 若每个请求都 asyncio.run()，共享的 httpx.AsyncClient 连接池会绑定到
# "上一个已关闭的事件循环"上，表现为第二次调用必失败（Event loop is closed）。
# 解决：进程内常驻一个事件循环 + 线程，所有异步调用提交到它执行。

import asyncio
import threading
from typing import Any


class AgentRuntime:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro, timeout: float = 30.0) -> Any:
        """把协程提交到常驻循环并等待结果。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
