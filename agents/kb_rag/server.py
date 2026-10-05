# agents/kb_rag/server.py
# 企业知识库 RAG Agent（A2A 独立服务 :5015）——A2A 外接、与主链路解耦。
#   能力：ask —— 检索 office_kb（BGE-M3 稠密+稀疏混合 + WeightedRanker 1.0/0.7）
#                → DeepSeek 依据上下文回答 → 带来源返回。
#   降级：Milvus / 模型不可用时返回 FAILED，主链路捕获后只降级、不 500。
#
# 运行：python agents/kb_rag/server.py   → http://localhost:5015

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from python_a2a import A2AServer, AgentCard, AgentSkill, Task, run_server  # noqa: E402

from agents.common.contract import TaskRequest, failed, render_result  # noqa: E402
from agents.common.runtime import AgentRuntime  # noqa: E402
from agents.kb_rag.embedder import BGEMEmbedder  # noqa: E402
from agents.kb_rag.store import KBStore  # noqa: E402
from backend.core.llm_factory import ainvoke_with_fallback  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

AGENT_NAME = "OfficeKbRagAgent"
PORT = 5015

RAG_SYSTEM_PROMPT = """你是公司内部知识库助手。只依据下面的「知识库内容」回答员工的问题。

规则：
1. 回答简洁、分点，并注明制度名称（如"《差旅与报销制度》"）；
2. 如果知识库内容不足以回答，只回复：知识库中没有找到相关制度，建议联系行政（分机 8002）或 IT（分机 8000）确认。
3. 不要编造制度中不存在的内容。

【知识库内容】
{contexts}
"""

CARD = AgentCard(
    name=AGENT_NAME,
    description="企业知识库问答（独立部署）：行政 / IT / 财务制度检索，带来源回答",
    url=f"http://localhost:{PORT}",
    version="1.0.0",
    capabilities={"streaming": False, "memory": False},
    skills=[
        AgentSkill(name="policy_lookup",
                   description="制度 / 流程查询（报销、借用、报修、入职）",
                   examples=["报销流程是什么", "器材借用期限是多久"]),
        AgentSkill(name="faq_search",
                   description="办公常见问题检索",
                   examples=["会议室预订要提前多久"]),
    ],
)


class OfficeKbRagAgent(A2AServer):
    def __init__(self):
        super().__init__(agent_card=CARD)
        self.runtime = AgentRuntime()

    async def _handle(self, req: TaskRequest) -> dict:
        q = (req.query or "").strip()
        if not q:
            return {"state": "input_required",
                    "clarify": "请告诉我你要查的制度或问题，例如：报销流程是什么。"}

        embedder = BGEMEmbedder.get_instance()
        dense, sparse = await asyncio.to_thread(embedder.encode_query, q)
        docs = await asyncio.to_thread(KBStore.hybrid_search, dense, sparse, 5)

        if not docs:
            return {"state": "completed",
                    "answer": "知识库中没有找到相关内容，建议换个问法，或联系行政（分机 8002）确认。",
                    "data": {"sources": []}}

        contexts = "\n\n".join(f"[来源：{d['source_name']}]\n{d['content']}" for d in docs)
        messages = [
            SystemMessage(content=RAG_SYSTEM_PROMPT.replace("{contexts}", contexts)),
            HumanMessage(content=q),
        ]
        resp = await ainvoke_with_fallback("rag", messages)
        answer = (getattr(resp, "text", None) or getattr(resp, "content", "") or "").strip()

        sources = [
            {"source_name": d["source_name"], "snippet": d["content"][:120],
             "score": round(d["score"], 4)}
            for d in docs[:3]
        ]
        logger.info("kb_rag.answered", question=q[:40], hits=len(docs))
        return {"state": "completed", "answer": answer, "data": {"sources": sources}}

    def handle_task(self, task: Task) -> Task:
        try:
            req = TaskRequest.parse(task)
            result = self.runtime.run(self._handle(req), timeout=60)
        except Exception as e:  # noqa: BLE001
            logger.error("kb_rag.exception", error=str(e)[:300])
            return failed(task, message=f"知识库服务异常：{e}")
        return render_result(task, result)


if __name__ == "__main__":
    configure_logging()
    print(f"{AGENT_NAME} A2A → http://localhost:{PORT}")
    print("  预热：加载 BGE-M3 模型 + 连接 Milvus（约 10~20 秒）...")
    BGEMEmbedder.get_instance()
    KBStore.client()
    print("  预热完成")
    agent = OfficeKbRagAgent()
    print(f"  skills: {[s.name for s in CARD.skills]}")
    run_server(agent, host="127.0.0.1", port=PORT)
