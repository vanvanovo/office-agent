# scripts/smoke_llm.py
# P0 收尾：DeepSeek 连通性冒烟（在 .env 填好 DEEPSEEK_API_KEY 后运行）
#
# 运行：python scripts/smoke_llm.py

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.core.llm_factory import ainvoke_with_fallback  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402


async def main() -> int:
    configure_logging()
    log = get_logger("smoke_llm")
    s = get_settings()

    if not s.deepseek_api_key:
        print("[skip] DEEPSEEK_API_KEY 为空：请在 .env 填入后重跑本脚本。")
        return 2

    resp = await ainvoke_with_fallback("intent", [HumanMessage(content="只回复两个字：正常")])
    text = getattr(resp, "text", None) or getattr(resp, "content", "")
    print(f"[llm] model={s.deepseek_model} reply={str(text)[:60]!r}")
    log.info("smoke.llm.passed", model=s.deepseek_model)
    print("[smoke] LLM 冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
