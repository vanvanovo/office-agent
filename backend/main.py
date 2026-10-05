# backend/main.py
# uvicorn backend.main:app --reload --port 8010

import os
import sys

# Windows conda 专用：把 base 的 Library/bin 加入 DLL 搜索路径（非 Windows 自动跳过）
if sys.platform == "win32":
    _conda_base_lib_bin = os.path.normpath(
        os.path.join(os.path.dirname(sys.executable), "..", "..", "Library", "bin")
    )
    if os.path.isdir(_conda_base_lib_bin):
        os.add_dll_directory(_conda_base_lib_bin)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.router import api_router
from backend.config import get_settings
from backend.core.logger import configure_logging, get_logger

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = get_logger(__name__)
    logger.info("app.starting", env=settings.app_env, port=settings.app_port)

    # P9：站内提醒调度器（会前提醒 / 工单超时）
    scheduler = None
    if settings.reminder_enabled:
        try:
            from backend.core.reminders import build_scheduler, run_meeting_reminders, run_ticket_reminders
            scheduler = build_scheduler()
            scheduler.start()
            # 启动后先跑一次，便于演示立即看到效果
            import asyncio as _asyncio
            async def _prime():
                await _asyncio.sleep(5)
                try:
                    await run_meeting_reminders()
                    await run_ticket_reminders()
                except Exception as e:  # noqa: BLE001
                    logger.warning("reminder.prime_failed", error=str(e)[:150])
            _asyncio.create_task(_prime())
            logger.info("app.reminders_started", interval=settings.reminder_interval_seconds)
        except Exception as e:  # noqa: BLE001
            logger.warning("app.reminders_start_failed", error=str(e)[:200])
            scheduler = None

    yield

    logger.info("app.shutting_down")
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    try:
        from backend.core.llm_factory import LLMFactory
        LLMFactory.clear_cache()
    except Exception:  # noqa: BLE001
        pass
    logger.info("app.shutdown_complete")


app = FastAPI(
    title="Office Agent API",
    description="多智能体办公助手 API（A2A + MCP）",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS：允许前端开发端口跨域访问（3010 为本项目 Vite 端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3010", "http://127.0.0.1:3010",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["系统"])
async def health_check():
    return {"status": "ok", "env": settings.app_env, "service": "office-agent"}
