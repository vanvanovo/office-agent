# backend/config.py
# 全项目唯一的配置中心：从项目根目录 .env 读取所有配置，任何模块经 get_settings() 取用。

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/ 的上一级）
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_project_root, ".env")


class Settings(BaseSettings):
    """配置模型：每个字段对应 .env 里的一项（大小写不敏感）。"""

    # ── 应用基础 ──
    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8010
    log_level: str = "INFO"
    upload_dir: str = "./data/uploads"

    # ── MySQL（三库）──
    mysql_host: str = "localhost"
    mysql_port: int = 3309            # 3307/3308 已被其他项目占用，隔离到 3309
    mysql_user: str = "office"
    mysql_password: str = ""
    mysql_db_oa: str = "office_oa"        # 模拟 OA 数据（会议室/预订）
    mysql_db_asset: str = "office_asset"  # 器材台账 + 报修工单
    mysql_db_app: str = "office_app"      # 员工/审计/站内信/兜底池

    @property
    def mysql_oa_url(self) -> str:
        return (f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db_oa}?charset=utf8mb4")

    @property
    def mysql_asset_url(self) -> str:
        return (f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db_asset}?charset=utf8mb4")

    @property
    def mysql_app_url(self) -> str:
        return (f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db_app}?charset=utf8mb4")

    # ── 大模型（provider 链：默认只启用 deepseek；备用 provider 配齐才启用）──
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_fallback_enabled: bool = False
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""

    # ── 知识库（Milvus + BGE-M3）──
    milvus_host: str = "localhost"
    milvus_port: int = 19532          # 19531 为 cs-platform 保留，本项目用 19532
    kb_collection: str = "office_kb"
    bge_m3_model_path: str = ""       # 绝对路径；留空时构建/检索会提示配置

    # ── 内部服务地址 ──
    mock_internal_url: str = "http://localhost:8210"
    oa_mcp_url: str = "http://localhost:8111/mcp"
    asset_mcp_url: str = "http://localhost:8112/mcp"
    mcp_shared_key: str = ""                      # MCP 服务间鉴权（空 = 不启用）
    meeting_query_agent_url: str = "http://localhost:5011"
    meeting_book_agent_url: str = "http://localhost:5012"
    equipment_query_agent_url: str = "http://localhost:5013"
    equipment_repair_agent_url: str = "http://localhost:5014"
    rag_agent_url: str = "http://localhost:5015"

    # ── 超时 / 重试 ──
    mcp_timeout_seconds: int = 5
    a2a_timeout_seconds: int = 15
    llm_timeout_seconds: int = 120
    llm_retry_times: int = 3

    # ── 站内提醒（APScheduler）──
    reminder_enabled: bool = True
    reminder_interval_seconds: int = 60
    reminder_lead_minutes: int = 15

    # ── 安全（JWT）──
    jwt_secret_key: str = "change-me-in-dotenv"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 10080

    model_config = SettingsConfigDict(
        env_file=_env_path,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """获取全局唯一的配置对象（带缓存，只读一次 .env）。"""
    return Settings()
