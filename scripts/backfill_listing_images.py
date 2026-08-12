#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回填上架表/选品表的商品图片 + SKU明细 —— 把存量行的真实图真正显示进多维表格。

背景:表B(上架情况表)新建了 图1~图9 附件字段 + SKU明细;表A(选品表)新建了
主图图片 附件字段 + SKU明细。存量行没有这些值,本脚本逐行补齐:
  - 表B 每行: 按 SPU 查平台库商品 → 图1~图9(image_urls 前 9 张)+ SKU明细
  - 表A 每行: 按 SPU 查平台库商品 → 主图图片(main_image_url)+ SKU明细
图片真实下载→上传飞书拿 file_token→写附件字段;单图失败留空该列不中断。
SPU 查不到 / 商品已软删(active=False)的行跳过,不改动。

用法(平台根目录下):
    python scripts/backfill_listing_images.py
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import selectinload  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.feishu import bitable  # noqa: E402
from app.feishu.bitable import _tokens  # noqa: E402
from app.models import Product  # noqa: E402


def _product_index() -> dict:
    """spu -> {urls, main, detail}。只含 active=True 的商品(软删的不回填)。"""
    db = SessionLocal()
    try:
        products = (db.query(Product)
                    .filter(Product.active == True)
                    .options(selectinload(Product.skus))
                    .all())
        index = {}
        for p in products:
            urls = p.image_urls or ([p.main_image_url] if p.main_image_url else [])
            index[p.spu] = {
                "urls": urls or [],
                "main": p.main_image_url or "",
                "detail": bitable.sku_detail_lines(p.skus),
            }
        return index
    finally:
        db.close()


def _fill_listing(record_id, urls, detail):
    """表B 一行: 图1~图9 + SKU明细。返回 (填图张数, 写SKU与否)。"""
    n = bitable.fill_record_images(record_id, urls or [])
    wrote_sku = 0
    if detail:
        bitable.batch_update(
            [{"record_id": record_id,
              "fields": {bitable.SKU_DETAIL_FIELD: detail}}])
        wrote_sku = 1
    return n, wrote_sku


def _fill_pick(record_id, main, detail, pick_tid):
    """表A 一行: 主图图片 + SKU明细。返回 (填图张数, 写SKU与否)。

    table_id 必须显式传选品表,否则默认写进上架表(表B),表A 的 record_id
    在表B里不存在 → record not found(实测踩过)。"""
    n = bitable.fill_record_images(
        record_id, [main] if main else [],
        field_names=[bitable.PICK_MAIN_IMAGE_FIELD], table_id=pick_tid)
    wrote_sku = 0
    if detail:
        bitable.batch_update(
            [{"record_id": record_id,
              "fields": {bitable.SKU_DETAIL_FIELD: detail}}],
            table_id=pick_tid)
        wrote_sku = 1
    return n, wrote_sku


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # 1. 幂等建字段
    print("== 建字段 ==", flush=True)
    c_listing = bitable.ensure_listing_image_columns()
    c_pick = bitable.ensure_pick_visual_columns()
    print(f"  表B 新建 {c_listing} 个字段 / 表A 新建 {c_pick} 个字段", flush=True)

    app_token, pick_tid, listing_tid = _tokens()
    index = _product_index()
    print(f"  平台库 active 商品索引: {len(index)} 个", flush=True)

    # 2. 组任务
    listing_jobs, pick_jobs = [], []
    for row in bitable.list_records([], page_size=100, table_id=listing_tid):
        spu = str((row.get("fields") or {}).get("SPU") or "").strip()
        info = index.get(spu)
        if not info:
            print(f"  [表B] {spu or '(无SPU)'}: 跳过(平台库无此商品或已删除)", flush=True)
            continue
        listing_jobs.append((row["record_id"], info["urls"], info["detail"]))
    for row in bitable.list_records([], page_size=100, table_id=pick_tid):
        spu = str((row.get("fields") or {}).get("SPU") or "").strip()
        info = index.get(spu)
        if not info:
            print(f"  [表A] {spu or '(无SPU)'}: 跳过(平台库无此商品或已删除)", flush=True)
            continue
        pick_jobs.append((row["record_id"], info["main"], info["detail"]))
    print(f"  任务: 表B {len(listing_jobs)} 行 / 表A {len(pick_jobs)} 行", flush=True)

    # 3. 并发回填
    print("== 回填中(真实下载+上传飞书,约 1~2 分钟)==", flush=True)
    ok = skips = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = []
        for record_id, urls, detail in listing_jobs:
            futs.append((f"表B {record_id}", ex.submit(_fill_listing, record_id, urls, detail)))
        for record_id, main, detail in pick_jobs:
            futs.append((f"表A {record_id}",
                         ex.submit(_fill_pick, record_id, main, detail, pick_tid)))
        for label, fut in futs:
            try:
                n, wrote_sku = fut.result()
                ok += 1
                print(f"  [{label}]: 图 {n} 张,SKU明细 {'写入' if wrote_sku else '无'}", flush=True)
            except Exception as exc:
                skips += 1
                print(f"  [{label}]: 回填失败 {exc}", flush=True)

    print(f"== 回填完成: 成功 {ok} 行 / 失败 {skips} 行 ==", flush=True)
    return 0 if skips == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
