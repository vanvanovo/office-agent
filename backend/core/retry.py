# backend/core/retry.py
# 三层兜底机制：自动重试 → Agent 级降级 → 系统级兜底（办公助手版）
#
# 设计口径：重试 3 次，指数退避 1s / 2s / 4s；重试仍失败 → 降级话术 → 系统兜底永不无响应。

import asyncio
from functools import wraps
from typing import Any, Callable, Optional

from backend.core.exceptions import (
    A2ACallError,
    AuthenticationError,
    InvalidInputError,
    LLMAPIError,
    MCPCallError,
    MySQLConnectionError,
)
from backend.core.logger import configure_logging, get_logger

logger = get_logger(__name__)

# ── 异常分类 ──
RETRYABLE_ERRORS = (LLMAPIError, MCPCallError, A2ACallError, MySQLConnectionError, TimeoutError, ConnectionError)
NON_RETRYABLE_ERRORS = (InvalidInputError, AuthenticationError)

MAX_RETRIES = 3                  # 最多重试 3 次（加上首次 = 共 4 次尝试）
RETRY_DELAYS = [1.0, 2.0, 4.0]   # 指数退避：1s / 2s / 4s
TIMEOUT_PER_ATTEMPT = 30.0       # 单次调用最多等 30 秒


def with_retry(agent_type: str = ""):
    """三层兜底装饰器工厂：给异步函数套上「重试 → 降级 → 系统兜底」。

    用法：
        @with_retry(agent_type="meeting_book")
        async def _invoke():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # ── 第一层：自动重试 ──
            last_error: Optional[Exception] = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    result = await asyncio.wait_for(func(*args, **kwargs), timeout=TIMEOUT_PER_ATTEMPT)
                    if attempt > 0:
                        logger.info("retry.succeeded", agent_type=agent_type, attempt=attempt + 1)
                    return result
                except NON_RETRYABLE_ERRORS as e:
                    logger.warning("retry.non_retryable_error", agent_type=agent_type, error=str(e))
                    raise
                except Exception as e:  # noqa: BLE001 —— 其余按可重试处理
                    last_error = e
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[attempt]
                        logger.warning("retry.attempt_failed", agent_type=agent_type,
                                       attempt=attempt + 1, max_retries=MAX_RETRIES, delay=delay, error=str(e))
                        await asyncio.sleep(delay)
                    else:
                        logger.error("retry.all_attempts_failed", agent_type=agent_type, error=str(e))

            # ── 第二层：Agent 级降级 ──
            try:
                fallback_result = await AgentFallbackHandler.handle(agent_type=agent_type, original_error=last_error)
                logger.info("retry.fallback_succeeded", agent_type=agent_type)
                return fallback_result
            except Exception as fallback_error:  # noqa: BLE001
                logger.error("retry.fallback_failed", agent_type=agent_type, error=str(fallback_error))

            # ── 第三层：系统级兜底 ──
            logger.error("retry.system_fallback", agent_type=agent_type, original_error=str(last_error))
            return _system_fallback_response(agent_type)

        return wrapper

    return decorator


class AgentFallbackHandler:
    """第二层降级：各业务环节的专项降级策略（保留核心语义，不无脑报错）。"""

    @classmethod
    async def handle(cls, agent_type: str, original_error: Exception) -> Any:
        fallback_map = {
            "intent":            cls._intent_fallback,
            "meeting_query":     cls._meeting_query_fallback,
            "meeting_book":      cls._meeting_book_fallback,
            "equipment_query":   cls._equipment_query_fallback,
            "equipment_repair":  cls._equipment_repair_fallback,
            "rag":               cls._rag_fallback,
        }
        handler = fallback_map.get(agent_type)
        if handler:
            return await handler()
        raise original_error

    @classmethod
    async def _intent_fallback(cls) -> dict:
        logger.info("fallback.intent_simple")
        return {"fallback_used": True,
                "content": "我暂时没理解你的问题，可以换个说法；也可以联系行政/IT 人工处理。"}

    @classmethod
    async def _meeting_query_fallback(cls) -> dict:
        logger.info("fallback.meeting_query_unavailable")
        return {"fallback_used": True,
                "content": "会议室查询服务暂时不可用，请稍后重试；如紧急请联系行政。"}

    @classmethod
    async def _meeting_book_fallback(cls) -> dict:
        logger.info("fallback.meeting_book_unavailable")
        return {"fallback_used": True,
                "content": "会议室预订服务暂时不可用，请稍后重试；如紧急请联系行政。"}

    @classmethod
    async def _equipment_query_fallback(cls) -> dict:
        logger.info("fallback.equipment_query_unavailable")
        return {"fallback_used": True,
                "content": "器材台账服务暂时不可用，请稍后重试；可联系 IT 人工查询。"}

    @classmethod
    async def _equipment_repair_fallback(cls) -> dict:
        logger.info("fallback.equipment_repair_unavailable")
        return {"fallback_used": True,
                "content": "报修服务暂时不可用，你的描述已记录；请稍后重试或直接联系 IT。"}

    @classmethod
    async def _rag_fallback(cls) -> dict:
        logger.info("fallback.rag_unavailable")
        return {"fallback_used": True,
                "content": "知识库服务暂时不可用，请稍后重试；已记录本次问题。"}


def _system_fallback_response(agent_type: str) -> dict:
    """第三层：系统级兜底（永不失败的最后一道）。"""
    messages = {
        "intent":           "非常抱歉，助手服务暂时不可用，请稍后再试。",
        "meeting_query":    "非常抱歉，会议室查询暂时不可用，请稍后再试或联系行政。",
        "meeting_book":     "非常抱歉，会议室预订暂时不可用，请稍后再试或联系行政。",
        "equipment_query":  "非常抱歉，器材查询暂时不可用，请稍后再试或联系 IT。",
        "equipment_repair": "非常抱歉，报修服务暂时不可用，请稍后再试或直接联系 IT。",
        "rag":              "非常抱歉，知识库服务暂时不可用，请稍后再试。",
    }
    content = messages.get(agent_type, "非常抱歉，服务暂时不可用，请稍后再试。")
    return {"fallback_used": True, "system_fallback": True, "content": content}


if __name__ == "__main__":
    # 自测：重试三次后走降级
    configure_logging()
    calls = {"n": 0}

    @with_retry(agent_type="meeting_book")
    async def always_fail():
        calls["n"] += 1
        raise LLMAPIError("模拟模型超时")

    async def main():
        result = await always_fail()
        print("calls:", calls["n"], "| result:", result)

    asyncio.run(main())
