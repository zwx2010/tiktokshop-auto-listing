"""文案范例语料:合并各 run 的 codex_listing_copy.csv。

语料即真实上架文案(few-shot 参考),来源:
- 平台工作流包 runs/&lt;run&gt;/&lt;market&gt;/codex_listing_copy.csv
- 本地化包 runs/&lt;run&gt;/&lt;market&gt;/codex_listing_copy.csv
每行含中文原题 + 三语 title/description/keywords。
"""
import csv
from pathlib import Path

from .io import workflow_dir


def iter_corpus_files():
    """按目录名/文件名去重,避免同一文件被扫两次。"""
    wd = workflow_dir()
    bases = [wd, wd.parent / "RoseSeek_TikTokShop_AI_Localized_20260809"]
    seen, out = set(), []
    for base in bases:
        for p in (base / "runs").glob("**/codex_listing_copy.csv"):
            key = p.resolve()
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def load_corpus():
    """读全部语料为 [{market_code, category, style_code, source_title_cn,
    title, description, keywords, listing_language}]。空 title 的行丢弃。"""
    out = []
    for path in iter_corpus_files():
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    if not (row.get("title") or "").strip():
                        continue
                    out.append({
                        "market_code": (row.get("market_code") or "").strip().upper(),
                        "category": (row.get("category") or "").strip(),
                        "style_code": (row.get("style_code") or "").strip(),
                        "source_title_cn": (row.get("source_title_cn") or "").strip(),
                        "title": (row.get("title") or "").strip(),
                        "description": (row.get("description") or "").strip(),
                        "keywords": (row.get("keywords") or "").strip(),
                        "listing_language": (row.get("listing_language") or "").strip(),
                    })
        except Exception:
            continue
    return out


def corpus_stats():
    c = load_corpus()
    by_market = {}
    for x in c:
        by_market[x["market_code"]] = by_market.get(x["market_code"], 0) + 1
    return {"total": len(c), "files": len(iter_corpus_files()),
            "by_market": by_market}
