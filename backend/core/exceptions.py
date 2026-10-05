# backend/core/exceptions.py
# 统一异常体系：所有自定义异常继承同一基类，便于「一次捕获全部」与按「可重试 / 不可重试」分类。

class OfficeAgentBaseError(Exception):
    """所有自定义异常的基类。额外携带：agent_type（哪个环节出错）与 details（细节字典）。"""

    def __init__(self, message: str, agent_type: str = "", details: dict | None = None):
        super().__init__(message)
        self.agent_type = agent_type
        self.details = details or {}


class LLMAPIError(OfficeAgentBaseError):
    """大模型 API 调用失败（超时 / 限流 / 网络错误）。【可重试】"""


class AgentExecutionError(OfficeAgentBaseError):
    """Agent 业务逻辑执行失败。"""


class IntentRouteError(OfficeAgentBaseError):
    """意图识别路由失败（没判断出该交给哪个 Agent）。"""


class MCPCallError(OfficeAgentBaseError):
    """MCP 工具调用失败（超时 / 连接失败 / 业务错误）。【可重试】"""


class A2ACallError(OfficeAgentBaseError):
    """A2A Agent 调用失败（超时 / 连接失败 / 任务非完成态）。【可重试】"""


class MySQLConnectionError(OfficeAgentBaseError):
    """MySQL 连接失败。【可重试】"""


class InvalidInputError(OfficeAgentBaseError):
    """用户输入不合法。【不可重试】"""


class AuthenticationError(OfficeAgentBaseError):
    """认证失败。【不可重试】"""
