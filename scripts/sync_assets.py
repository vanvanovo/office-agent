# scripts/sync_assets.py
# 器材台账每日同步任务（演示版手动触发；生产可挂 cron / Windows 计划任务 / APScheduler）
#   来源：Mock 内部系统 GET /asset/ledger （模拟甲方资产系统的台账接口）
#   去向：office_asset.equipment（我方台账，UPSERT 幂等）
#   记录：office_asset.sync_log（批次 / 条数 / 耗时 / 状态）
#
# 运行：python scripts/sync_assets.py

import asyncio
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


async def main() -> int:
    configure_logging()
    s = get_settings()
    batch_id = f"SYNC{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
    t0 = time.time()

    # ① 从外部资产系统拉全量台账
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
            resp = await c.get(f"{s.mock_internal_url}/asset/ledger")
            resp.raise_for_status()
            items = resp.json().get("items", [])
    except Exception as e:  # noqa: BLE001
        print(f"[sync] 拉取外部台账失败：{e}")
        return 1

    # ② UPSERT 进我方台账 + ③ 写同步日志
    ok = failed = 0
    duration = 0
    engine = create_async_engine(s.mysql_asset_url)
    try:
        async with engine.begin() as conn:
            for it in items:
                try:
                    await conn.execute(text("""
                        INSERT INTO equipment
                            (asset_id, name, category, brand, model, department, owner_id,
                             status, borrowable, static_updated_at)
                        VALUES (:asset_id, :name, :category, :brand, :model, :department, :owner_id,
                                :status, :borrowable, NOW())
                        ON DUPLICATE KEY UPDATE
                            name=VALUES(name), category=VALUES(category), brand=VALUES(brand),
                            model=VALUES(model), department=VALUES(department), owner_id=VALUES(owner_id),
                            status=VALUES(status), borrowable=VALUES(borrowable), static_updated_at=NOW()
                    """), it)
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    logger.warning("sync.item_failed", asset_id=it.get("asset_id"), error=str(e)[:150])

            duration = int((time.time() - t0) * 1000)
            await conn.execute(text("""
                INSERT INTO sync_log (batch_id, source, total, ok_count, failed, duration_ms, status)
                VALUES (:b, 'mock_asset_system', :t, :ok, :f, :d, :st)
            """), {"b": batch_id, "t": len(items), "ok": ok, "f": failed, "d": duration,
                   "st": "ok" if failed == 0 else "partial"})
    finally:
        await engine.dispose()

    print(f"[sync] batch={batch_id} total={len(items)} ok={ok} failed={failed} duration={duration}ms")
    logger.info("sync.done", batch_id=batch_id, total=len(items), ok=ok, failed=failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
