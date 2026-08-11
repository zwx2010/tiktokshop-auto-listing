"""混合 RAG 知识层。

- 规则层 rules.py:价格 / 违禁 / 品类 / 风格 —— 结构化硬过滤(采集/选品)
- 向量层 vector.py:文案范例 BM25+向量 混合检索(文案 few-shot 注入)
- corpus.py / io.py:语料合并与带 BOM 容错的读取

给 agent 用的统一入口在 cli.py(`python -m app.rag.cli check/copy`)。
"""
from .rules import evaluate_product, check_price, check_banned, check_category, supported_markets  # noqa
from .vector import retrieve_copy, build_index, Embedder  # noqa
from .corpus import load_corpus  # noqa

__all__ = [
    "evaluate_product", "check_price", "check_banned", "check_category",
    "supported_markets", "retrieve_copy", "build_index", "Embedder", "load_corpus",
]
