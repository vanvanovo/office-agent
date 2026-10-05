# agents/kb_rag/store.py
# office_kb 集合的写入与混合检索（BGE-M3 稠密 + 稀疏 → WeightedRanker 融合）。
#
# 权重口径（设计约定）：稠密 1.0 / 稀疏 0.7。

from __future__ import annotations

import hashlib
from typing import Optional

from pymilvus import AnnSearchRequest, MilvusClient, WeightedRanker

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)
VECTOR_DIM = 1024


def _uri() -> str:
    s = get_settings()
    return f"http://{s.milvus_host}:{s.milvus_port}"


def _collection() -> str:
    return get_settings().kb_collection


class KBStore:
    _client: Optional[MilvusClient] = None

    @classmethod
    def client(cls) -> MilvusClient:
        if cls._client is None:
            cls._client = MilvusClient(uri=_uri())
            try:
                cls._client.load_collection(_collection())
            except Exception:  # noqa: BLE001 —— 集合未建时忽略，检索时会报明确错误
                pass
            logger.info("kb_milvus.connected", uri=_uri(), collection=_collection())
        return cls._client

    @staticmethod
    def chunk_id(content: str, document_id: str, chunk_index: int) -> str:
        return hashlib.md5(f"{document_id}_{chunk_index}_{content[:50]}".encode()).hexdigest()

    @classmethod
    def delete_document(cls, document_id: str) -> None:
        safe = document_id.replace('"', '\\"')
        try:
            cls.client().delete(collection_name=_collection(),
                                filter=f'document_id == "{safe}"')
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_milvus.delete_failed", document_id=document_id, error=str(e)[:150])

    @classmethod
    def upsert(cls, rows: list[dict]) -> int:
        if not rows:
            return 0
        cls.client().upsert(collection_name=_collection(), data=rows)
        return len(rows)

    @classmethod
    def hybrid_search(cls, dense: list[float], sparse: dict, top_k: int = 5) -> list[dict]:
        dense_req = AnnSearchRequest(
            data=[dense], anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}}, limit=top_k)
        sparse_req = AnnSearchRequest(
            data=[sparse], anns_field="sparse_embedding",
            param={"metric_type": "IP"}, limit=top_k)

        results = cls.client().hybrid_search(
            collection_name=_collection(),
            reqs=[dense_req, sparse_req],
            ranker=WeightedRanker(1.0, 0.7),          # 稠密 1.0 / 稀疏 0.7
            limit=top_k,
            output_fields=["content", "source_name", "chunk_type", "chunk_index"],
        )
        docs = []
        for hit in results[0]:
            entity = hit.get("entity") or {}
            docs.append({
                "content": entity.get("content") or "",
                "source_name": entity.get("source_name") or "",
                "chunk_type": entity.get("chunk_type") or "text",
                "chunk_index": entity.get("chunk_index") or 0,
                "score": float(hit.get("distance") or 0.0),
            })
        logger.info("kb_milvus.search_done", hits=len(docs))
        return docs
