# scripts/init_office_milvus.py
# 创建 office_kb 集合（幂等：存在则重建；重建后需重跑 build_office_kb.py）。
#
# 运行：python scripts/init_office_milvus.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymilvus import DataType, MilvusClient  # noqa: E402

from backend.config import get_settings  # noqa: E402

VECTOR_DIM = 1024


def build_schema(client: MilvusClient):
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=VECTOR_DIM)
    schema.add_field("sparse_embedding", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("content", DataType.VARCHAR, max_length=8192)
    schema.add_field("source_name", DataType.VARCHAR, max_length=256)
    schema.add_field("chunk_type", DataType.VARCHAR, max_length=32)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("document_id", DataType.VARCHAR, max_length=64)
    schema.add_field("updated_at", DataType.INT64)
    return schema


def build_index_params(client: MilvusClient):
    ip = client.prepare_index_params()
    ip.add_index(field_name="embedding", index_type="HNSW", metric_type="COSINE",
                 params={"M": 16, "efConstruction": 256})
    ip.add_index(field_name="sparse_embedding", index_type="SPARSE_INVERTED_INDEX",
                 metric_type="IP", params={"drop_ratio_build": 0.2})
    ip.add_index(field_name="document_id", index_type="INVERTED")
    return ip


def main() -> int:
    s = get_settings()
    uri = f"http://{s.milvus_host}:{s.milvus_port}"
    name = s.kb_collection
    print(f"连接 Milvus：{uri} | 集合：{name}")
    client = MilvusClient(uri=uri)

    if client.has_collection(name):
        print(f"删除旧集合 '{name}' ...")
        client.drop_collection(name)

    client.create_collection(collection_name=name,
                             schema=build_schema(client),
                             index_params=build_index_params(client))
    print(f"集合 '{name}' 创建完成（含索引）；集合列表：{client.list_collections()}")
    print("提示：集合已重建，请运行 python scripts/build_office_kb.py 导入知识库文档。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
