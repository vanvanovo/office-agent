# scripts/init_db.py
# 初始化三个库的表结构（幂等：CREATE TABLE IF NOT EXISTS）。
#
# 运行：python scripts/init_db.py
#
# 库归属：
#   office_oa    —— 模拟公司 OA 系统（会议室 / 预订 / 资产动态状态）
#   office_asset —— 我方台账（器材静态属性 / 报修工单 / 同步日志）
#   office_app   —— 应用数据（员工 / 写审计 / 站内信 / 兜底池）

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

DDL_OA = [
    """
    CREATE TABLE IF NOT EXISTS rooms (
        room_id    VARCHAR(32) PRIMARY KEY,
        building   VARCHAR(16) NOT NULL,
        floor      INT NOT NULL,
        name       VARCHAR(64) NOT NULL,
        capacity   INT NOT NULL,
        equipment  JSON NULL,
        active     TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS bookings (
        booking_id      VARCHAR(40) PRIMARY KEY,
        room_id         VARCHAR(32) NOT NULL,
        date            DATE NOT NULL,
        start_time      TIME NOT NULL,
        end_time        TIME NOT NULL,
        employee_id     VARCHAR(32) NOT NULL,
        purpose         VARCHAR(255) NULL,
        status          VARCHAR(16) NOT NULL DEFAULT 'booked',
        series_id       VARCHAR(40) NULL,
        idempotency_key VARCHAR(64) NULL,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_bookings_idem (idempotency_key),
        KEY idx_bookings_room_date (room_id, date, status),
        KEY idx_bookings_emp (employee_id, date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_availability (
        asset_id       VARCHAR(32) PRIMARY KEY,
        borrowable_now TINYINT(1) NOT NULL DEFAULT 1,
        holder         VARCHAR(64) NULL,
        due_back       DATETIME NULL,
        updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_ledger_source (
        asset_id   VARCHAR(32) PRIMARY KEY,
        name       VARCHAR(64) NOT NULL,
        category   VARCHAR(32) NOT NULL,
        brand      VARCHAR(32) NULL,
        model      VARCHAR(64) NULL,
        department VARCHAR(32) NULL,
        owner_id   VARCHAR(32) NULL,
        status     VARCHAR(16) NOT NULL DEFAULT 'in_stock',
        borrowable TINYINT(1) NOT NULL DEFAULT 1,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

DDL_ASSET = [
    """
    CREATE TABLE IF NOT EXISTS equipment (
        asset_id          VARCHAR(32) PRIMARY KEY,
        name              VARCHAR(64) NOT NULL,
        category          VARCHAR(32) NOT NULL,
        brand             VARCHAR(32) NULL,
        model             VARCHAR(64) NULL,
        department        VARCHAR(32) NULL,
        owner_id          VARCHAR(32) NULL,
        status            VARCHAR(16) NOT NULL DEFAULT 'in_stock',
        borrowable        TINYINT(1) NOT NULL DEFAULT 1,
        static_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_equipment_cat (category, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS repair_tickets (
        ticket_no       VARCHAR(40) PRIMARY KEY,
        asset_id        VARCHAR(32) NULL,
        fault_desc      TEXT NULL,
        urgency         VARCHAR(8) NOT NULL DEFAULT 'medium',
        reporter_id     VARCHAR(32) NOT NULL,
        status          VARCHAR(16) NOT NULL DEFAULT 'open',
        attachments     JSON NULL,
        urged_at        DATETIME NULL,
        idempotency_key VARCHAR(64) NULL,
        sla_due_at      DATETIME NULL,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_repair_idem (idempotency_key),
        KEY idx_repair_reporter (reporter_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_log (
        id          BIGINT AUTO_INCREMENT PRIMARY KEY,
        batch_id    VARCHAR(40) NULL,
        source      VARCHAR(32) NULL,
        total       INT NULL,
        ok_count    INT NULL,
        failed      INT NULL,
        duration_ms INT NULL,
        status      VARCHAR(16) NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

DDL_APP = [
    """
    CREATE TABLE IF NOT EXISTS employees (
        emp_id        VARCHAR(32) PRIMARY KEY,
        name          VARCHAR(32) NOT NULL,
        role          VARCHAR(16) NOT NULL DEFAULT 'employee',
        password_hash VARCHAR(255) NOT NULL,
        department    VARCHAR(32) NULL,
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS write_audit (
        id              BIGINT AUTO_INCREMENT PRIMARY KEY,
        action          VARCHAR(32) NOT NULL,
        target          VARCHAR(64) NULL,
        actor           VARCHAR(32) NULL,
        idempotency_key VARCHAR(64) NULL,
        result          VARCHAR(16) NULL,
        detail          JSON NULL,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_audit_actor (actor),
        KEY idx_audit_action (action)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS station_messages (
        msg_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
        emp_id     VARCHAR(32) NOT NULL,
        type       VARCHAR(32) NOT NULL,
        title      VARCHAR(128) NOT NULL,
        payload    JSON NULL,
        is_read    TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_msg_emp (emp_id, is_read)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS unresolved_requests (
        id              BIGINT AUTO_INCREMENT PRIMARY KEY,
        emp_id          VARCHAR(32) NULL,
        question        TEXT NULL,
        reason          VARCHAR(32) NULL,
        context_summary TEXT NULL,
        status          VARCHAR(16) NOT NULL DEFAULT 'open',
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_unresolved_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


async def _run_ddl(url: str, statements: list[str], label: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            for stmt in statements:
                await conn.execute(text(stmt))
        logger.info("init_db.tables_ready", db=label, tables=len(statements))
        print(f"[ok] {label}: {len(statements)} 张表就绪")
    finally:
        await engine.dispose()


async def main() -> None:
    configure_logging()
    s = get_settings()
    print(f"[db] {s.mysql_host}:{s.mysql_port} user={s.mysql_user}")
    await _run_ddl(s.mysql_oa_url, DDL_OA, s.mysql_db_oa)
    await _run_ddl(s.mysql_asset_url, DDL_ASSET, s.mysql_db_asset)
    await _run_ddl(s.mysql_app_url, DDL_APP, s.mysql_db_app)
    print("[done] 三库表结构就绪")


if __name__ == "__main__":
    asyncio.run(main())
