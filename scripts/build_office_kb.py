# scripts/build_office_kb.py
# 把 data/kb/*.md 制度文档切块 → BGE-M3 嵌入（稠密+稀疏）→ 写入 Milvus office_kb。
#
# 运行：python scripts/build_office_kb.py
# 依赖：Milvus 已启动 + 已运行 init_office_milvus.py + .env 配置 BGE_M3_MODEL_PATH

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.kb_rag.embedder import BGEMEmbedder  # noqa: E402
from agents.kb_rag.store import KBStore  # noqa: E402
from backend.core.logger import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)
ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "data" / "kb"

MAX_CHUNK = 500        # 单块最大字符数
MIN_CHUNK = 60         # 过短的块并入上一块


def split_markdown(text: str, max_len: int = MAX_CHUNK) -> list[tuple[str, str]]:
    """按二级标题分段 → 段内按段落聚合到 <= max_len。返回 [(section_title, chunk_text)]。"""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    title, buf = "概述", []
    for line in lines:
        if line.startswith("## "):
            if buf:
                sections.append((title, buf))
            title, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if buf:
        sections.append((title, buf))

    chunks: list[tuple[str, str]] = []
    for sec_title, sec_lines in sections:
        paragraph = "\n".join(sec_lines).strip()
        if not paragraph:
            continue
        # 段落聚合
        cur = ""
        for para in [p.strip() for p in paragraph.split("\n\n") if p.strip()]:
            if len(cur) + len(para) + 2 <= max_len:
                cur = (cur + "\n\n" + para).strip()
            else:
                if cur:
                    chunks.append((sec_title, cur))
                cur = para
        if cur:
            chunks.append((sec_title, cur))

    # 过短块并入前一块
    merged: list[tuple[str, str]] = []
    for sec, chunk in chunks:
        if merged and len(chunk) < MIN_CHUNK:
            prev_sec, prev = merged[-1]
            merged[-1] = (prev_sec, prev + "\n" + chunk)
        else:
            merged.append((sec, chunk))
    return merged


def main() -> int:
    configure_logging()
    files = sorted(KB_DIR.glob("*.md"))
    if not files:
        print(f"未找到知识库文档：{KB_DIR}")
        return 1

    print(f"[1/3] 加载嵌入模型（首次约 10~20 秒）...")
    embedder = BGEMEmbedder.get_instance()

    total_chunks = 0
    t0 = time.time()
    for f in files:
        doc_title = f.stem
        document_id = f.stem.split("-", 1)[-1]          # 稳定 id（去掉编号前缀）
        text = f.read_text(encoding="utf-8")
        chunks = split_markdown(text)
        if not chunks:
            continue

        print(f"[2/3] 切块 {f.name}：{len(chunks)} 块")
        contents = [c for _, c in chunks]
        dense_list, sparse_list = embedder.encode(contents, batch_size=8)

        rows = []
        for idx, ((sec, content), dense, sparse) in enumerate(zip(chunks, dense_list, sparse_list)):
            rows.append({
                "id": KBStore.chunk_id(content, document_id, idx),
                "embedding": dense,
                "sparse_embedding": sparse,
                "content": content[:4000],
                "source_name": f"{document_id} > {sec}",
                "chunk_type": "text",
                "chunk_index": idx,
                "document_id": document_id,
                "updated_at": int(time.time()),
            })
        KBStore.delete_document(document_id)             # 幂等重建：先删后插
        n = KBStore.upsert(rows)
        total_chunks += n
        print(f"      已写入 {n} 块（来源：{doc_title}）")

    print(f"[3/3] 完成：{len(files)} 篇文档 / {total_chunks} 块 / 耗时 {time.time() - t0:.1f}s")
    logger.info("kb_build.done", docs=len(files), chunks=total_chunks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
