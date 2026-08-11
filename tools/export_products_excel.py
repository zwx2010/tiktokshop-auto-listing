#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导出采集商品为 Excel（运营管理用）。

用法：
    python tools/export_products_excel.py

输出：
    data/products_YYYYMMDD_HHMMSS.xlsx

内容与看板「导出Excel」按钮完全一致（共用 app.excel_export.build_workbook）：
    Sheet「商品」：一行一个商品，含成本/图片数/主图URL(可点击)/已上架店铺数/采集时间(北京时间)
    Sheet「SKU明细」：一个商品的多规格逐行展开，含颜色/成本/库存
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.excel_export import build_workbook  # noqa: E402


def main():
    db = SessionLocal()
    try:
        wb = build_workbook(db)
    finally:
        db.close()

    if wb["商品"].max_row <= 1:
        print("库里还没有商品，先跑采集再导出。")
        return

    data_dir = Path(__file__).resolve().parent.parent / "data"
    out = data_dir / f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(out)
    n = wb["商品"].max_row - 1
    print(f"已导出 {n} 个商品 -> {out}")


if __name__ == "__main__":
    main()
