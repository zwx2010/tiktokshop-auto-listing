#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""平台库真实文案 → RAG 语料(codex_listing_copy.csv)。

把平台库已生成的本地化 Listing(英/泰/越三语,listing_status=ready 且标题非空)
按市场导出为 corpus.py 可扫的 codex_listing_copy.csv(列与工作流 codex 一致),
让 RAG 向量索引用真实上架文案做 few-shot 范例 —— 不掺假,全部来自平台库真实数据。

列对齐 app/rag/corpus.load_corpus 读的 8 列:
    market_code / category / style_code / source_title_cn /
    title / description / keywords / listing_language

keywords 平台库无独立字段,导出留空(工作流 codex 的 keywords 同样是空的)。

用法:
    python tools/export_corpus_from_platform.py
    python tools/export_corpus_from_platform.py --run-dir D:\\...\\tk自动化工作流\\runs\\platform_corpus_20260813
"""
import argparse
import csv
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Listing, Product  # noqa: E402
from sqlalchemy import func  # noqa: E402

# 市场 → 文案语言(与 export_platform_to_staging 同源;平台 Listing 无语言字段,按市场推导)
LANGUAGES = {"PH": "English", "TH": "Thai", "VN": "Vietnamese"}

COLUMNS = [
    "market_code", "category", "style_code", "source_title_cn",
    "title", "description", "keywords", "listing_language",
]


def resolve_workflow_dir(args) -> Path:
    if args.workflow_dir:
        return Path(args.workflow_dir)
    env = os.environ.get("TIKTOK_WORKFLOW_DIR")
    if env:
        return Path(env)
    base = Path(__file__).resolve().parent.parent
    return base.parent / "tk自动化工作流"


def load_corpus_rows(db):
    """平台库真实文案 → 语料行(仅导出标题非空、商品未软删的 Listing)。"""
    q = (
        db.query(Product, Listing)
        .join(Listing, Listing.product_id == Product.id)
        .filter(
            Product.active == True,
            Listing.title != "",
            Listing.title.isnot(None),
        )
        .order_by(Product.id, Listing.market_code)
    )
    rows = []
    for p, l in q.all():
        rows.append({
            "market_code": (l.market_code or "").strip().upper(),
            "category": (p.category or "").strip(),
            "style_code": (p.source_goods_id or "").strip(),
            "source_title_cn": (p.title_cn or "").strip(),
            "title": (l.title or "").strip(),
            "description": (l.description or "").strip(),
            "keywords": "",
            "listing_language": LANGUAGES.get(
                (l.market_code or "").strip().upper(), "English"),
        })
    return rows


def write_corpus(rows, run_dir: Path) -> dict[str, Path]:
    """按市场分文件写 utf-8-sig CSV,返回 {market: path}。"""
    by_market: dict[str, list[dict]] = {}
    for r in rows:
        by_market.setdefault(r["market_code"], []).append(r)
    written: dict[str, Path] = {}
    for mkt in sorted(by_market):
        d = run_dir / mkt.lower()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "codex_listing_copy.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(by_market[mkt])
        written[mkt] = path
    return written


def export_from_platform(run_dir: Path | None = None,
                         workflow_dir: Path | None = None) -> dict:
    """导出平台库真实文案到语料目录,供 RAG 使用。

    每次写新时间戳目录 → 语料文件集合变化 → 下次检索 ensure_index 自动重建
    向量索引,无需手动跑 build_rag_index。可被流水线上架完成回调直接调用。

    返回 {"written": {mkt: Path}, "total": int, "products": int,
          "by_market": {mkt: int}, "run_dir": Path}。
    """
    if workflow_dir is None:
        workflow_dir = resolve_workflow_dir(argparse.Namespace(workflow_dir=None))
    db = SessionLocal()
    try:
        rows = load_corpus_rows(db)
        n_prod = (db.query(func.count(Product.id))
                  .filter(Product.active == True).scalar())
    finally:
        db.close()
    if not rows:
        raise ValueError("平台库没有标题非空的 Listing,未导出")
    run_dir = run_dir or (
        workflow_dir / "runs" / f"platform_corpus_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    # 只保留最新一个 platform_corpus_* 目录:语料按来源全量重导(平台库是活数据源),
    # 旧目录不删会累积重复内容,检索 top-k 被重复挤占、索引无限膨胀。
    runs_root = workflow_dir / "runs"
    if runs_root.is_dir():
        for d in runs_root.glob("platform_corpus_*"):
            if d.resolve() != run_dir.resolve():
                shutil.rmtree(d, ignore_errors=True)
    written = write_corpus(rows, run_dir)
    by_market: dict[str, int] = {}
    for r in rows:
        by_market[r["market_code"]] = by_market.get(r["market_code"], 0) + 1
    return {"written": written, "total": len(rows), "products": n_prod,
            "by_market": by_market, "run_dir": run_dir}


def main() -> None:
    ap = argparse.ArgumentParser(description="平台库真实文案 → RAG 语料")
    ap.add_argument("--workflow-dir", default=None, help="工作流根目录(默认自动推导)")
    ap.add_argument("--run-dir", default=None,
                    help="输出目录(默认 {工作流}/runs/platform_corpus_时间戳)")
    args = ap.parse_args()
    try:
        res = export_from_platform(
            run_dir=Path(args.run_dir) if args.run_dir else None,
            workflow_dir=resolve_workflow_dir(args))
    except ValueError as e:
        print(e)
        sys.exit(1)
    written = res["written"]
    print(f"平台库 active 商品 {res['products']} 件,导出 {res['total']} 条真实文案:")
    for mkt in sorted(written):
        print(f"  {mkt:<3} {res['by_market'][mkt]:>3} 条 -> {written[mkt]}")
    print(f"语料目录: {res['run_dir']}")
    print("下一步: python -m scripts.build_rag_index --force 重建索引")


if __name__ == "__main__":
    main()
