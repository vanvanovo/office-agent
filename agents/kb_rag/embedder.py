# agents/kb_rag/embedder.py
# BGE-M3 嵌入模型（进程内单例）：一次推理输出 dense（1024 维）+ sparse（{token_id: weight}）。
# 模型权重路径来自 .env 的 BGE_M3_MODEL_PATH（演示环境复用客服平台的模型目录，只读）。

from __future__ import annotations

import os
from typing import Optional

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)


class BGEMEmbedder:
    _instance: Optional["BGEMEmbedder"] = None

    def __init__(self, model_path: str):
        # FlagEmbedding 1.3.x 依赖 transformers 内部函数（保险补丁）
        import importlib.util as _ilu
        from transformers.utils import import_utils as _tf_iu
        if not hasattr(_tf_iu, "is_torch_fx_available"):
            _tf_iu.is_torch_fx_available = lambda: _ilu.find_spec("torch.fx") is not None

        import torch
        from FlagEmbedding import BGEM3FlagModel

        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                f"BGE-M3 模型目录不存在：{model_path}（请在 .env 配置 BGE_M3_MODEL_PATH）")

        logger.info("bge_m3.loading", model_path=model_path)
        self._model = BGEM3FlagModel(model_name_or_path=model_path,
                                     use_fp16=torch.cuda.is_available())
        logger.info("bge_m3.loaded", fp16=torch.cuda.is_available())

    @classmethod
    def get_instance(cls) -> "BGEMEmbedder":
        if cls._instance is None:
            path = (get_settings().bge_m3_model_path or "").strip()
            if not path:
                raise RuntimeError("BGE_M3_MODEL_PATH 未配置（可指向客服平台模型目录或自行下载）")
            cls._instance = BGEMEmbedder(os.path.abspath(path))
        return cls._instance

    def encode(self, texts: list[str], batch_size: int = 12) -> tuple[list[list[float]], list[dict]]:
        output = self._model.encode(
            texts,
            batch_size=batch_size,
            max_length=8192,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense = output["dense_vecs"].tolist()
        sparse = [{int(k): float(v) for k, v in d.items()} for d in output["lexical_weights"]]
        return dense, sparse

    def encode_query(self, text: str) -> tuple[list[float], dict]:
        dense, sparse = self.encode([text], batch_size=1)
        return dense[0], sparse[0]
