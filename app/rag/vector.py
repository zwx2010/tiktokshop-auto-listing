"""向量层:文案范例的混合检索(BM25 + 向量余弦)。

设计决策:
- 语料小(数十~几百条),索引放 SQLite(`data/rag_corpus.db`),查询在内存算 ——
  零服务、可离线;不引 sqlite-vec 原生扩展(兼容性风险)。
- Embedding 双后端:
  1. API:千问 DashScope text-embedding(真语义向量),config/embedding.local.json 可配;
  2. 本地确定性 feature-hash(crc32 分词),无网/无 key 时兜底,同一文本必得同一向量。
- 混合分 = w_vec * cosine + w_bm25 * bm25(权重建表时固定)。
- 索引按语料文件 mtime 自动过期重建(build_index / ensure_index)。
"""
import json
import math
import re
import sqlite3
import urllib.request
import zlib
from functools import lru_cache
from pathlib import Path

from ..config import DATA_DIR
from . import corpus as _corpus
from .io import read_json, workflow_dir

VEC_DIM = 512
BM25_K1 = 1.5
BM25_B = 0.75
WEIGHTS = {"vector": 0.6, "bm25": 0.4}
DB_PATH = Path(DATA_DIR) / "rag_corpus.db"


# ---------------------------------------------------------------- 分词
def _tokenize(text):
    """中文按 单字+双字 切,英文/数字按词切。中文不依赖分词库。"""
    toks = []
    for run in re.findall(r"[一-鿿]+|[a-z0-9]+", (text or "").lower()):
        if re.search(r"[一-鿿]", run):
            chars = list(run)
            toks.extend(chars)
            if len(chars) >= 2:
                toks.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
        else:
            toks.append(run)
    return toks


# ---------------------------------------------------------------- Embedding
class Embedder:
    """本地确定性 feature-hash 向量(兜底,保证离线/无 key 也能跑)。"""

    def __init__(self, dim=VEC_DIM):
        self.dim = dim

    def embed(self, text):
        vec = [0.0] * self.dim
        for tok in _tokenize(text):
            h = zlib.crc32(tok.encode("utf-8"))
            vec[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    @staticmethod
    def default():
        """优先真语义向量(ApiEmbedder 有配置时),否则本地哈希兜底。"""
        api = ApiEmbedder.from_config()
        return api if api else Embedder()


class ApiEmbedder(Embedder):
    """千问 DashScope text-embedding,OpenAI 兼容 /embeddings 端点。"""

    def __init__(self, endpoint, key, model="text-embedding-v3", dim=VEC_DIM):
        super().__init__(dim)
        self.endpoint = endpoint
        self.key = key
        self.model = model

    def embed(self, text):
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps({"model": self.model, "input": text}).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vec = data["data"][0]["embedding"]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    @staticmethod
    def from_config():
        """读 config/embedding.local.json(或 qwen_api.local.json 的 embedding 字段)。"""
        from ..config import CONFIG_DIR
        emb = None
        p = CONFIG_DIR / "embedding.local.json"
        if p.is_file():
            emb = read_json(p)
        if not emb:
            q = CONFIG_DIR / "qwen_api.local.json"
            if q.is_file():
                emb = (read_json(q) or {}).get("embedding")
        if emb and emb.get("endpoint") and emb.get("key"):
            return ApiEmbedder(emb["endpoint"], emb["key"],
                               emb.get("model", "text-embedding-v3"))
        return None


# ---------------------------------------------------------------- BM25
def _bm25_scores(docs, query_tokens):
    n = len(docs)
    if not n:
        return []
    lens = [len(d["tokens"]) for d in docs]
    avg = sum(lens) / n or 1.0
    df = {}
    for d in docs:
        for t in set(d["tokens"]):
            df[t] = df.get(t, 0) + 1
    scores = []
    for i, d in enumerate(docs):
        tf = {}
        for t in d["tokens"]:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in set(query_tokens):
            f = df.get(t, 0)
            if f and t in tf:
                idf = math.log(1 + (n - f + 0.5) / (f + 0.5))
                k = BM25_K1 * (1 - BM25_B + BM25_B * lens[i] / avg)
                s += idf * (tf[t] * (BM25_K1 + 1)) / (tf[t] + k)
        scores.append(s)
    mx = max(scores) or 1.0
    return [s / mx for s in scores]


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------- 索引
def _doc_text(x):
    """一份范例的检索文本:中文原题 + 英文文案 + 关键词。"""
    return " ".join((x["source_title_cn"], x["title"], x["keywords"]))


def build_index(force=False, embedder=None):
    """重建向量索引。返回 (条数, backend 名)。幂等,语料没变则跳过。"""
    embedder = embedder or Embedder.default()
    corpus = _corpus.load_corpus()
    db = sqlite3.connect(DB_PATH)
    try:
        cur = db.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS corpus_embeddings("
                    "id INTEGER PRIMARY KEY, market_code TEXT, category TEXT, "
                    "style_code TEXT, meta TEXT, vector TEXT)")
        cur.execute("CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT)")
        meta = {k: v for k, v in cur.execute("SELECT key,value FROM index_meta")}
        src = meta.get("sources", "")
        if not force and src == json.dumps(_corpus.iter_corpus_files(), default=str):
            return len(corpus), embedder.__class__.__name__
        cur.execute("DELETE FROM corpus_embeddings")
        for x in corpus:
            cur.execute(
                "INSERT INTO corpus_embeddings(market_code,category,style_code,meta,vector)"
                " VALUES(?,?,?,?,?)",
                (x["market_code"], x["category"], x["style_code"],
                 json.dumps(x, ensure_ascii=False),
                 json.dumps(embedder.embed(_doc_text(x)))))
        # 过期判断只靠 sources(语料文件集合):避免引入时钟依赖
        cur.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES('sources',?)",
                    (json.dumps(_corpus.iter_corpus_files(), default=str),))
        db.commit()
        return len(corpus), embedder.__class__.__name__
    finally:
        db.close()


def ensure_index():
    """查询前保证索引存在(按语料文件集合判断过期)。"""
    db = sqlite3.connect(DB_PATH)
    try:
        cur = db.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT)")
        src = dict(cur.execute("SELECT key,value FROM index_meta")).get("sources", "")
        current = json.dumps(_corpus.iter_corpus_files(), default=str)
        if src != current:
            build_index(force=True)
    finally:
        db.close()


# ---------------------------------------------------------------- 检索
CATEGORY_BOOST = 0.12  # 品类命中加分(软过滤:语料没该品类也能借到相近范例)


def retrieve_copy(market, category=None, style_code=None, title="", top_k=3,
                  category_strict=False):
    """按市场(硬)/品类(软加分,默认)/风格(硬) 混合检索,返回 top_k 范例。

    软过滤设计:语料没覆盖某品类(如项链)时,仍返回语义最近的范例供 few-shot,
    命中品类的加 CATEGORY_BOOST 排前面。category_strict=True 则品类硬过滤。

    返回 [{title, description, keywords, category, market_code, style_code, score}]。
    """
    ensure_index()
    market = (market or "").upper()
    docs = []
    db = sqlite3.connect(DB_PATH)
    try:
        cur = db.cursor()
        cur.execute("SELECT market_code,category,style_code,meta,vector "
                    "FROM corpus_embeddings")
        for mkt, cat, sty, meta, vec in cur.fetchall():
            if market and mkt != market:
                continue
            if style_code and sty != style_code:
                continue
            cat_hit = (category and category.lower() in (cat or "").lower())
            if category_strict and category and not cat_hit:
                continue
            x = json.loads(meta)
            x["tokens"] = _tokenize(_doc_text(x))
            x["_vec"] = json.loads(vec)
            x["_cat_hit"] = bool(cat_hit)
            docs.append(x)
    finally:
        db.close()

    if not docs:
        return []

    query_text = (title or "") + " " + (category or "") + " " + market
    query_tokens = _tokenize(query_text)
    embedder = Embedder.default()
    q_vec = embedder.embed(query_text)

    bm25 = _bm25_scores(docs, query_tokens)
    scored = []
    for i, d in enumerate(docs):
        v = _cosine(q_vec, d["_vec"])
        hybrid = WEIGHTS["vector"] * v + WEIGHTS["bm25"] * bm25[i]
        if d["_cat_hit"]:
            hybrid += CATEGORY_BOOST
        scored.append((hybrid, i))
    scored.sort(key=lambda p: p[0], reverse=True)

    out = []
    for hybrid, i in scored[:top_k]:
        d = docs[i]
        out.append({
            "title": d["title"],
            "description": d["description"],
            "keywords": d["keywords"],
            "category": d["category"],
            "market_code": d["market_code"],
            "style_code": d["style_code"],
            "source_title_cn": d["source_title_cn"],
            "score": round(hybrid, 4),
        })
    return out
