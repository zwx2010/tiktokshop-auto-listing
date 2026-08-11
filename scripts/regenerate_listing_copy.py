#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重生成真实文案 —— 把 Mock 假文案 Listing 替换为真实来源，并用 RAG 规则把关。

背景：旧版 get_coze_client() 默认返回 MockCozeClient，导致入库的 Listing 标题/描述
是编造的模板文案（title 含 "Daily Outfit Styling"、description 含 "Elevate your daily look"）。
本脚本扫描这些假文案，逐个用真实来源重生成：
  1. codex_listing_copy.csv（真实三语文案表，按市场）
  2. claude 桥分批生成（--mode llm）—— 生成时注入 RAG 要点规则 + 真实文案范例：
     - app.rag.rules 的结构化规则（违禁词/品类白名单/上传规格上限）压成提示词片段；
     - app.rag.vector.retrieve_copy 检索同市场真实范例做 few-shot（风格/本地化参考）；
     全部从商品真实属性生成，禁止编造。
  3. 都不可用 → 清空 title/description 并标 listing_status='copy_missing'
     （export 检测不到文案会跳过该件，绝不带假/空文案上架）

RAG 索引先用 build_rag_index.py 建（数据落 data/rag_corpus.db）；retrieve_copy 查询前会自动 ensure_index。

用法：
    python -m scripts.build_rag_index --force        # 先生成 RAG 索引（也可省略，查询时自动建）
    python scripts/regenerate_listing_copy.py --dry-run          # 只统计，不改库
    python scripts/regenerate_listing_copy.py --mode auto        # codex + copy_missing（默认）
    python scripts/regenerate_listing_copy.py --mode llm         # codex + claude 桥分批生成
    python scripts/regenerate_listing_copy.py --mode llm --limit 3   # 限量试跑
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import selectinload  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Account, Listing, Product  # noqa: E402

DEFAULT_STORE = "RoseSeek"
_LANGS = {"PH": "English", "TH": "Thai", "VN": "Vietnamese"}


def _workflow_dir() -> Path:
    """与 export_platform_to_staging.py 同源：工作流根目录（含 codex 表）。"""
    env = os.environ.get("TIKTOK_WORKFLOW_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "tk自动化工作流"


_WF = _workflow_dir()


def _load_codex(market: str) -> dict[str, dict]:
    """读 {wf}/runs/smoke_01/{market}/codex_listing_copy.csv，key=goods_id。"""
    p = _WF / "runs" / "smoke_01" / market.lower() / "codex_listing_copy.csv"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        gid = str(r.get("goods_id") or "").strip()
        if gid and (r.get("title") or r.get("description")):
            out[gid] = r
    return out


def _is_mock(lst) -> bool:
    t = (lst.title or "").upper()
    d = (lst.description or "").upper()
    return "DAILY OUTFIT STYLING" in t or "ELEVATE YOUR DAILY LOOK" in d


def _needs_copy(lst) -> bool:
    """需要补文案：Mock 假文案 / 已标 copy_missing / 空文案。"""
    if lst.listing_status == "copy_missing":
        return True
    if not (lst.title and lst.description):
        return True
    return _is_mock(lst)


def _rag_rules_summary() -> str:
    """把规则层要点压成提示词片段（违禁/品类白名单/上传规格）。"""
    from app.rag import rules
    cfg = rules._cfg()
    lines = []
    banned = []
    banned += (cfg["brand"].get("blocked_brand_patterns") or [])
    banned += (cfg["filters"].get("blocked_title_patterns") or [])
    banned += (cfg["audience"].get("blocked_title_patterns") or [])
    banned = [b for b in banned if b]
    if banned:
        lines.append("违禁词（标题/描述中禁止出现）: " + "、".join(list(dict.fromkeys(banned))[:15]))
    req = [p for p in (cfg["filters"].get("required_title_patterns") or []) if p]
    if req:
        lines.append("品类白名单（标题须命中其一）: " + "、".join(req))
    for r in rules.upload_spec_rules():
        if r.get("kind") == "max_length" and r.get("column"):
            lines.append("上传规格: %s 长度≤%s 字符（%s）" % (
                r.get("column"), r.get("max_len"), r.get("tiktok_error") or "TikTok 会拒收"))
    return "\n".join(lines) or "（无额外硬性规则）"


def _rag_examples(market: str, product, top_k=2) -> list[dict]:
    """检索同市场真实文案范例做 few-shot。失败返回空列表，不中断生成。"""
    from app.rag import retrieve_copy
    try:
        return retrieve_copy(market, product.category, None, product.title_cn, top_k)
    except Exception:
        return []


def _llm_batch_prompt(entries: list[dict], store: str, rules_text: str) -> str:
    """entries: [{"goods_id","style_code","title_cn","category","colors","styles",
                  "markets":[...], "examples": {MARKET: [{title,description}]}}]"""
    lines = []
    for i, e in enumerate(entries):
        lines.append(
            f"{i}. goods_id={e['goods_id']} style_code={e['style_code']} 中文标题={e['title_cn']} "
            f"类目={e['category']} 颜色={e['colors']} 款式={e['styles']} 需要的站点={','.join(e['markets'])}"
        )
    ex_lines = []
    for e in entries:
        for mkt in e["markets"]:
            exs = e.get("examples", {}).get(mkt) or []
            if exs:
                ex_lines.append(f"[商品 {e['goods_id']} / {mkt} 真实范例]")
                for x in exs[:2]:
                    ex_lines.append(f"  title: {(x.get('title') or '')[:160]}")
                    ex_lines.append(f"  desc:  {(x.get('description') or '')[:200]}")
    langs = "，".join(f"{m}({_LANGS.get(m, m)})" for m in ("PH", "TH", "VN"))
    return (
        "你是 TikTok Shop 配饰类目本地化文案写手。为下面的商品生成真实可上架的 "
        f"{langs} 站点标题与描述。\n"
        "【平台要点规则，必须遵守】\n" + rules_text + "\n\n"
        "【真实文案范例，参考其本地化风格与用词习惯，不要照抄】\n"
        + ("\n".join(ex_lines) if ex_lines else "（暂无范例）") + "\n\n"
        "商品清单（每件的 style_code 已列出）：\n" + "\n".join(lines) + "\n"
        f"店铺名统一用 {store}。要求：每站标题以 {store} 开头、含品类词与关键词、"
        "必须以 ' Style {该商品的style_code}' 结尾——用商品清单里自己的 style_code，"
        "范例里的 Style 编号属于别的商品，绝对不要照抄。"
        "描述如实描述颜色/款式/日常使用场景，禁止编造材质、克重、规格、销量、承诺。\n"
        "只输出 JSON：{\"items\": [{\"goods_id\": \"...\", "
        "\"PH\": {\"title\": \"...\", \"description\": \"...\"}, "
        "\"TH\": {...}, \"VN\": {...}}]}"
    )


def _run_claude(prompt: str, timeout_s=240):
    from app.agent import bridge
    res = bridge.run_claude(prompt, timeout_s=timeout_s)
    if not res.get("ok"):
        return None
    sr = res.get("stage_result")
    return sr if isinstance(sr, dict) else None


def _sku_attrs(product):
    colors = " / ".join(s.color for s in product.skus if s.color and s.color != "Default")
    styles = " / ".join(s.style for s in product.skus if s.style)
    return (colors or "-"), (styles or "-")


def main() -> int:
    ap = argparse.ArgumentParser(description="重生成 Listing 真实文案（RAG 规则把关）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不改库")
    ap.add_argument("--mode", choices=["auto", "llm"], default="auto",
                    help="auto=codex+copy_missing；llm=再加 claude 桥分批生成")
    ap.add_argument("--market", default="", help="只处理某市场（ph/th/vn），留空=全部")
    ap.add_argument("--batch-size", type=int, default=1, help="每次 claude 调用处理的商品数（llm 模式）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个商品（0=不限）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = (
            db.query(Listing, Product)
            .join(Product, Listing.product_id == Product.id)
            .options(selectinload(Product.skus))
            .all()
        )
        if args.market:
            rows = [r for r in rows if r[0].market_code.upper() == args.market.upper()]
        needs = [r for r in rows if _needs_copy(r[0])]
        mocks = [r for r in rows if _is_mock(r[0])]
    finally:
        db.close()

    if not needs:
        print(json.dumps({"ok": True, "need_total": 0, "summary": "没有需要补文案的 Listing"},
                         ensure_ascii=False))
        return 0

    # 按商品聚合
    by_prod: dict[int, dict] = {}
    for lst, prod in needs:
        d = by_prod.setdefault(prod.id, {"product": prod, "listings": []})
        d["listings"].append(lst)

    codex = {m: _load_codex(m) for m in ("ph", "th", "vn")}

    if args.dry_run:
        codex_hit = 0
        for d in by_prod.values():
            prod = d["product"]
            gid = prod.source_goods_id or ""
            if any(gid in codex[l.market_code.lower()] for l in d["listings"]):
                codex_hit += 1
        print(json.dumps({
            "ok": True, "dry_run": True,
            "need_listings": len(needs), "need_products": len(by_prod),
            "mock_listings": len(mocks),
            "codex_covered_products": codex_hit,
            "need_llm_or_missing_products": len(by_prod) - codex_hit,
            "mode": args.mode,
            "summary": f"需补文案 {len(needs)} 条 / {len(by_prod)} 商品：codex 覆盖 {codex_hit} 商品，"
                       f"其余 {len(by_prod) - codex_hit} 商品需 claude 生成或标 copy_missing",
        }, ensure_ascii=False))
        return 0

    rules_text = _rag_rules_summary()

    # 逐商品重生成
    db = SessionLocal()
    done = {"codex": 0, "llm": 0, "missing": 0, "error": 0}
    processed = 0
    try:
        batch: list[dict] = []

        def flush_batch():
            nonlocal batch
            if not batch:
                return
            prompt = _llm_batch_prompt(batch, DEFAULT_STORE, rules_text)
            sr = _run_claude(prompt)
            items = (sr or {}).get("items") if isinstance(sr, dict) else None
            if not isinstance(items, list):
                print(f"  [LLM批失败] 本批 {len(batch)} 商品未返回合法 JSON —— 全标 copy_missing", flush=True)
                items = []
            item_map = {}
            for it in items:
                if isinstance(it, dict) and it.get("goods_id"):
                    item_map[str(it["goods_id"])] = it
            for e in batch:
                it = item_map.get(e["goods_id"])
                for lst_ref in e["listing_refs"]:
                    # 关键：lst_ref 来自已关闭的首个 session（detached），
                    # 直接改它再 commit 到本 session 会静默丢失（SQLAlchemy 不跟踪 detached 改动）。
                    # 必须在本活动 session 里按 id 重新拉取再写。
                    lst = db.get(Listing, lst_ref.id)
                    if lst is None:
                        continue
                    mkt = lst.market_code.upper()
                    row = (it or {}).get(mkt) or {}
                    title = (row.get("title") or "").strip()
                    desc = (row.get("description") or "").strip()
                    try:
                        if title and desc:
                            lst.title = title
                            lst.description = desc
                            lst.listing_status = "ready"
                            done["llm"] += 1
                        else:
                            lst.title = ""
                            lst.description = ""
                            lst.listing_status = "copy_missing"
                            done["missing"] += 1
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        done["error"] += 1
                        print(f"  [写库失败] {e['goods_id']}/{mkt}: {exc}", flush=True)
            batch = []

        for d in by_prod.values():
            if args.limit and processed >= args.limit:
                break
            processed += 1
            prod = d["product"]
            gid = prod.source_goods_id or ""
            colors, styles = _sku_attrs(prod)
            entry = {
                "goods_id": gid,
                "style_code": str(gid)[-4:],
                "title_cn": prod.title_cn,
                "category": prod.category,
                "colors": colors,
                "styles": styles,
                "markets": [],
                "examples": {},
                "listing_refs": [],
            }
            need_llm = False
            for lst_ref in d["listings"]:
                mkt = lst_ref.market_code.lower()
                row = codex[mkt].get(gid)
                if row and (row.get("title") or row.get("description")):
                    lst = db.get(Listing, lst_ref.id)
                    if lst is None:
                        continue
                    try:
                        lst.title = row["title"]
                        lst.description = row["description"]
                        lst.listing_status = "ready"
                        done["codex"] += 1
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        done["error"] += 1
                        print(f"  [写库失败-codex] {gid}/{mkt}: {exc}", flush=True)
                else:
                    need_llm = True
                    entry["markets"].append(lst_ref.market_code.upper())
                    entry["listing_refs"].append(lst_ref)
            if need_llm:
                # 预取 RAG 真实范例
                for mkt in entry["markets"]:
                    entry["examples"][mkt] = _rag_examples(mkt.lower(), prod)
                if args.mode == "llm":
                    batch.append(entry)
                    if len(batch) >= args.batch_size:
                        flush_batch()
                else:
                    for lst_ref in entry["listing_refs"]:
                        lst = db.get(Listing, lst_ref.id)
                        if lst is None:
                            continue
                        try:
                            lst.title = ""
                            lst.description = ""
                            lst.listing_status = "copy_missing"
                            done["missing"] += 1
                            db.commit()
                        except Exception as exc:
                            db.rollback()
                            done["error"] += 1
                            print(f"  [写库失败-missing] {gid}: {exc}", flush=True)
        flush_batch()
    finally:
        db.close()

    print(json.dumps({"ok": True, **done, "mode": args.mode,
                      "summary": f"codex {done['codex']} / claude {done['llm']} / "
                                 f"copy_missing {done['missing']} / 失败 {done['error']}"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
