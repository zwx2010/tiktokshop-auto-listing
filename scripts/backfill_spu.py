#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给历史商品回填 SPU 管理编号 —— 采集链路已改为入库时自动赋予 SPU{id:06d},
但改造前入库的商品 spu 为空。遍历一次,按 id 补齐,幂等(有 spu 的跳过)。

用法(平台根目录下):
    python -m scripts.backfill_spu
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.database import SessionLocal, engine  # noqa: E402
from app.models import Product  # noqa: E402


def _ensure_spu_column() -> None:
    """旧库 products 表可能没有 spu 列(SQLite create_all 不改已有表),这里补上。"""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text
    insp = sa_inspect(engine)
    cols = [c["name"] for c in insp.get_columns("products")]
    if "spu" not in cols:
        with engine.begin() as con:
            con.execute(text("ALTER TABLE products ADD COLUMN spu VARCHAR(32)"))


def main() -> int:
    _ensure_spu_column()
    db = SessionLocal()
    try:
        missing = db.query(Product).filter(
            (Product.spu == "") | (Product.spu.is_(None))
        ).order_by(Product.id).all()
        if not missing:
            print("库内商品都有 SPU,无需回填。")
            return 0
        for p in missing:
            p.spu = f"SPU{p.id:06d}"
        db.commit()
        print(f"回填完成: 为 {len(missing)} 件历史商品赋予 SPU。")
        for p in db.query(Product).order_by(Product.id).all():
            print(f"  {p.spu}  {p.source_goods_id}  {str(p.title_cn)[:20]}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
