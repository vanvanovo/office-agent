# scripts/smoke_p0.py
# P0 冒烟：验证配置加载、FastAPI 应用装配（/health + /api/v1/ping）、日志落盘。
#
# 运行：python scripts/smoke_p0.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.main import app  # noqa: E402


def main() -> int:
    s = get_settings()
    print(f"[config] app_port={s.app_port} mysql_port={s.mysql_port} "
          f"model={s.deepseek_model} key_set={bool(s.deepseek_api_key)}")

    with TestClient(app) as client:      # with 会触发 lifespan（初始化日志）
        r1 = client.get("/health")
        r2 = client.get("/api/v1/ping")
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        print(f"[health] {r1.status_code} {r1.json()}")
        print(f"[ping]   {r2.status_code} {r2.json()}")

    from backend.core.logger import get_logger
    log = get_logger("smoke")
    log.info("smoke.p0.passed")
    print("[smoke] P0 冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
