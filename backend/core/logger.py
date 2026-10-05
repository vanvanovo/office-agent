# backend/core/logger.py
# 结构化日志工具：在标准库 logging 之上包装「事件名 + 键值对」写法；控制台 + logs/app.log 双输出。

import logging
import os
import sys

from backend.config import get_settings

# 项目根目录（本文件位于 backend/core/ 下，上溯两级）
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Logger:
    """日志包装类：支持 logger.info("事件名", key=value) 的结构化写法。"""

    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    def _fmt(self, event: str, **kw) -> str:
        if kw:
            return event + " | " + " ".join(f"{k}={v!r}" for k, v in kw.items())
        return event

    def debug(self, event: str, **kw):
        self._log.debug(self._fmt(event, **kw))

    def info(self, event: str, *args, **kw):
        # 兼容两种写法：① logger.info("事件", key=value)；② logger.info("模板 %s", 值)
        if args:
            self._log.info(event, *args)
        else:
            self._log.info(self._fmt(event, **kw))

    def warning(self, event: str, *args, **kw):
        if args:
            self._log.warning(event, *args)
        else:
            self._log.warning(self._fmt(event, **kw))

    def error(self, event: str, **kw):
        exc_info = kw.pop("exc_info", False)
        self._log.error(self._fmt(event, **kw), exc_info=exc_info)

    def critical(self, event: str, **kw):
        self._log.critical(self._fmt(event, **kw))


def configure_logging() -> None:
    """全局日志配置：应用启动时调用一次；可重复调用（会重建 handler）。"""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):        # 清理旧 handler，保证幂等
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_dir = os.path.join(_project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, "app.log"), encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # 压低第三方库噪音
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "sqlalchemy.pool", "aiomysql", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> _Logger:
    """每个模块用 get_logger(__name__) 获取日志器。"""
    return _Logger(name)
