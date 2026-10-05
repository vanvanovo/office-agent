# backend/core/llm_factory.py
# LLM Factory：统一封装大模型调用，所有 Agent 必须经此模块获取模型。
# provider 链：默认只启用 DeepSeek；备用 provider 配置齐全且 LLM_FALLBACK_ENABLED=true 时自动切换。

from typing import Any, Type

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from backend.config import get_settings
from backend.core.exceptions import LLMAPIError
from backend.core.logger import get_logger

logger = get_logger(__name__)

# ── 自定义 httpx 客户端：绕过系统代理（DeepSeek 国内可直连，代理会导致 TLS 失败）──
_HTTP_ASYNC_CLIENT = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))
_HTTP_SYNC_CLIENT = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))

# ── Agent 类型 → 模型标识符路由表（想给某类业务换模型只改这里一行）──
_AGENT_MODEL_ROUTING: dict[str, str] = {
    "intent":           "deepseek-chat",   # 意图识别 / 槽位 / 追问
    "summarize":        "deepseek-chat",   # 结果整理 / 口语化
    "meeting_query":    "deepseek-chat",   # 会议室查询 Agent
    "meeting_book":     "deepseek-chat",   # 会议室预订 Agent
    "equipment_query":  "deepseek-chat",   # 器材查询 Agent
    "equipment_repair": "deepseek-chat",   # 器材报修 Agent
    "rag":              "deepseek-chat",   # 知识问答
}

# ── 模型标识符 → API 实际 model 名（deepseek-chat 映射为当前可用模型名）──
_MODEL_ID_MAP: dict[str, str] = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek-v4-pro",
}


class LLMFactory:
    """大模型工厂（统一获取模型的唯一入口）。用法：LLMFactory.get_llm("intent")。"""

    _instances: dict[str, BaseChatModel] = {}   # 模型实例缓存

    @classmethod
    def _resolve_model_id(cls, model_key: str) -> str:
        return _MODEL_ID_MAP.get(model_key, model_key)

    @classmethod
    def _build_primary_kwargs(cls, model_key: str) -> dict[str, Any]:
        s = get_settings()
        if not s.deepseek_api_key:
            raise LLMAPIError("DEEPSEEK_API_KEY 未配置，请在 .env 中填写", agent_type="llm")
        return {
            "model": cls._resolve_model_id(model_key),
            "model_provider": "openai",
            "api_key": s.deepseek_api_key,
            "base_url": s.deepseek_base_url,
            "max_retries": 0,                      # 模型层不重试，重试统一由 retry.py 管
            "http_async_client": _HTTP_ASYNC_CLIENT,
            "http_client": _HTTP_SYNC_CLIENT,
        }

    @classmethod
    def _build_fallback_kwargs(cls, model_key: str) -> dict[str, Any]:
        s = get_settings()
        return {
            "model": cls._resolve_model_id(model_key),
            "model_provider": "openai",
            "api_key": s.llm_fallback_api_key,
            "base_url": s.llm_fallback_base_url,
            "max_retries": 0,
            "http_async_client": _HTTP_ASYNC_CLIENT,
            "http_client": _HTTP_SYNC_CLIENT,
        }

    @classmethod
    def get_llm(cls, agent_type: str, temperature: float = 0, streaming: bool = False) -> BaseChatModel:
        """按 Agent 类型获取主 provider 模型实例（带缓存）。"""
        if agent_type not in _AGENT_MODEL_ROUTING:
            raise ValueError(f"未知 agent_type: '{agent_type}'，可用：{list(_AGENT_MODEL_ROUTING.keys())}")
        model_key = _AGENT_MODEL_ROUTING[agent_type]
        cache_key = f"primary_{model_key}_{temperature}_{streaming}"
        if cache_key not in cls._instances:
            kwargs = cls._build_primary_kwargs(model_key)
            kwargs["temperature"] = temperature
            kwargs["streaming"] = streaming
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}   # 关闭思考模式，避免正文为空
            cls._instances[cache_key] = init_chat_model(**kwargs)
            logger.info("llm_factory.model_initialized",
                        agent_type=agent_type, model_key=model_key, role="primary")
        return cls._instances[cache_key]

    @classmethod
    def _get_fallback_llm(cls, agent_type: str, temperature: float = 0,
                          streaming: bool = False) -> BaseChatModel:
        """获取备用 provider 模型（配置齐全且启用才可用）。"""
        s = get_settings()
        if not (s.llm_fallback_enabled and s.llm_fallback_api_key
                and s.llm_fallback_base_url and s.llm_fallback_model):
            raise LLMAPIError("备用 provider 未配置或未启用", agent_type=agent_type)
        model_key = s.llm_fallback_model
        cache_key = f"fallback_{model_key}_{temperature}_{streaming}"
        if cache_key not in cls._instances:
            kwargs = cls._build_fallback_kwargs(model_key)
            kwargs["temperature"] = temperature
            kwargs["streaming"] = streaming
            cls._instances[cache_key] = init_chat_model(**kwargs)
            logger.info("llm_factory.model_initialized",
                        agent_type=agent_type, model_key=model_key, role="fallback")
        return cls._instances[cache_key]

    @classmethod
    async def ainvoke_with_fallback(cls, agent_type: str, messages, temperature: float = 0.0):
        """调用主 provider；失败且备用 provider 可用时自动切换（容灾链）。"""
        llm = cls.get_llm(agent_type, temperature=temperature)
        try:
            return await llm.ainvoke(messages)
        except Exception as e:  # noqa: BLE001
            s = get_settings()
            if not s.llm_fallback_enabled:
                raise
            logger.warning("llm_factory.primary_failed_switch_fallback",
                           agent_type=agent_type, error=str(e)[:200])
            fb = cls._get_fallback_llm(agent_type, temperature=temperature)
            result = await fb.ainvoke(messages)
            logger.info("llm_factory.fallback_succeeded", agent_type=agent_type)
            return result

    @classmethod
    def get_structured_llm(cls, agent_type: str, output_schema: Type[BaseModel],
                           temperature: float = 0) -> Runnable:
        """获取绑定了结构化输出 Schema 的模型（直接返回 schema 对象）。"""
        llm = cls.get_llm(agent_type, temperature=temperature)
        return llm.with_structured_output(output_schema, method="function_calling")

    @classmethod
    def clear_cache(cls) -> None:
        cls._instances.clear()
        logger.info("llm_factory.cache_cleared")


# ── 模块级便捷入口 ──
def get_llm(agent_type: str, temperature: float = 0, streaming: bool = False) -> BaseChatModel:
    return LLMFactory.get_llm(agent_type, temperature=temperature, streaming=streaming)


def get_structured_llm(agent_type: str, output_schema: Type[BaseModel]) -> Runnable:
    return LLMFactory.get_structured_llm(agent_type, output_schema)


async def ainvoke_with_fallback(agent_type: str, messages, temperature: float = 0.0):
    return await LLMFactory.ainvoke_with_fallback(agent_type, messages, temperature=temperature)
