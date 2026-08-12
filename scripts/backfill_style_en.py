#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给 product_skus 补 style_en 列 + 从 codex 英文映射回填已覆盖的款式。

背景:上传表(上架表 xlsx)的「次要销售变体值」不能含中文,而 SKU.style 是
1688 采集来的中文款式名(如 马年鸿运礼盒)。新增 ProductSku.style_en 存英文款,
由文案本地化链路(claude 桥)生成;本脚本做两件事:
  1. 旧库 product_skus 表没有 style_en 列(SQLite create_all 不改已有表)→ 补列
  2. 从各市场 codex_listing_copy.csv 的 styles→styles_en 映射回填已覆盖的行
     (不在 codex 里的中文款,由 _ensure_styles_en / regenerate 链路后续翻译)

用法(平台根目录下):
    python -m scripts.backfill_style_en
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.database import SessionLocal, engine  # noqa: E402
from app.models import ProductSku  # noqa: E402


def _ensure_style_en_column() -> None:
    """旧库 product_skus 表可能没有 style_en 列,补上。"""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text
    insp = sa_inspect(engine)
    cols = [c["name"] for c in insp.get_columns("product_skus")]
    if "style_en" not in cols:
        with engine.begin() as con:
            con.execute(text("ALTER TABLE product_skus ADD COLUMN style_en VARCHAR(128)"))


def _codex_map():
    """各市场 codex_listing_copy.csv → {中文style: 英文style}。"""
    wf = Path(__file__).resolve().parents[1].parent / "tk自动化工作流"
    out = {}
    for market in ("ph", "th", "vn"):
        p = wf / "runs" / "smoke_01" / market / "codex_listing_copy.csv"
        if not p.is_file():
            continue
        import csv
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                cn = (r.get("styles") or "").strip()
                en = (r.get("styles_en") or "").strip()
                if cn and en:
                    for c, e in zip(cn.split("|"), en.split("|")):
                        c, e = c.strip(), e.strip()
                        if c and e:
                            out[c] = e
    return out


def main() -> int:
    _ensure_style_en_column()
    cmap = _codex_map()
    if not cmap:
        print("codex 映射为空(未找到 codex_listing_copy.csv 或全无 styles_en)。")
    db = SessionLocal()
    try:
        rows = db.query(ProductSku).filter(
            (ProductSku.style_en == "") | (ProductSku.style_en.is_(None))
        ).all()
        hit = 0
        for s in rows:
            en = cmap.get((s.style or "").strip())
            if en:
                s.style_en = en
                hit += 1
        db.commit()
        print(f"补列完成 + codex 回填 {hit} 行(仍有中文 style 未覆盖的行待 LLM 翻译)。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
