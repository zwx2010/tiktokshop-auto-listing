#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""1688 采集产物 → 平台库 platform.db 桥接脚本（采集入库功能核心）。

从 1688 CDP 采集 run 目录读逐件 capture JSON（captures/1688_capture_*.json，UTF-8-sig），
调用 app.pipeline.ingest_capture 写入平台库。幂等：dedup_key = {source_platform}:{goods_id}
（1688:...），重复灌入不产生脏数据，已存在的商品记为 already 跳过。

用法：
    python tools/import_1688_captures_to_db.py --run-dir <runs/1688_xxx> --market th
    python tools/import_1688_captures_to_db.py --auto-latest --market ph   # 最新一个 1688 run

设计：
- 确定性脚本，不经 LLM —— DB 写入是源头事实，结果如实打印，由编排层（approval.py）解析；
- 采集端标记 needs_review / 验证码 / 登录页 的件直接跳过，不入库；
- 单件失败不影响其余（逐件 rollback），errors 逐条列出，不做粉饰。
"""
import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Account, Product  # noqa: E402
from app.pipeline import ingest_capture  # noqa: E402

# 与 app/agent/tasks.py 同源：ROSEEK_PKG_DIR 可覆盖，缺省用项目根的相对路径
_PKG = os.environ.get(
    "ROSEEK_PKG_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "RoseSeek_TikTokShop_AI_Localized_20260809"),
)


def _resolve_run_dir(explicit: str) -> str:
    """解析 run 目录：显式路径(含 pkg 前缀)→ 不存在则找最新 1688 run。"""
    if explicit:
        for cand in (explicit, str(Path(_PKG) / explicit)):
            if os.path.isdir(cand):
                return cand
    base = Path(_PKG) / "runs"
    if base.is_dir():
        runs = sorted(
            (p for p in base.glob("1688_accessories_*") if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
        if runs:
            return str(runs[-1])
    return ""


def _account_for(db, market_upper: str):
    """按市场找账号；没有就建一个（与 routers/api._get_or_create_account 同思路）。"""
    acc = db.query(Account).filter(Account.market_code == market_upper).first()
    if acc:
        return acc
    acc = Account(name=f"RoseSeek_{market_upper}", platform="tiktok_shop",
                  market_code=market_upper, store_name="RoseSeek")
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def main() -> int:
    ap = argparse.ArgumentParser(description="1688 采集产物写进平台库")
    ap.add_argument("--run-dir", default="", help="1688 run 目录；留空则用最新一个 run")
    ap.add_argument("--auto-latest", action="store_true", help="总是用最新 1688 run")
    ap.add_argument("--market", default="th", help="入库市场 th/ph/vn（默认 th）")
    args = ap.parse_args()

    run_dir = _resolve_run_dir(args.run_dir if not args.auto_latest else "")
    if not run_dir:
        out = {"ok": False, "stage": "bridge_to_db", "imported": 0, "already": 0,
               "skipped": 0, "errors": [{"error": "找不到 1688 采集 run 目录"}],
               "run_dir": "", "summary": "找不到采集 run，未入库"}
        print(json.dumps(out, ensure_ascii=False))
        return 2

    caps_dir = Path(run_dir) / "captures"
    files = [f for f in sorted(glob.glob(str(caps_dir / "1688_capture_*.json")))
             if re.search(r"1688_capture_(\d+)\.json$", f)]
    if not files:
        out = {"ok": False, "stage": "bridge_to_db", "imported": 0, "already": 0,
               "skipped": 0, "errors": [{"error": f"captures 目录无逐件 JSON: {caps_dir}"}],
               "run_dir": run_dir, "summary": "run 目录无采集件，未入库"}
        print(json.dumps(out, ensure_ascii=False))
        return 2

    market_upper = (args.market or "th").upper()
    imported = already = skipped = 0
    errors = []
    db = SessionLocal()
    try:
        acc = _account_for(db, market_upper)
        for fp in files:
            m = re.search(r"1688_capture_(\d+)\.json$", fp)
            goods_id = m.group(1) if m else os.path.basename(fp)
            try:
                with open(fp, encoding="utf-8-sig") as fh:  # 1688 capture 是 UTF-8 BOM
                    cap = json.load(fh)
            except Exception as e:
                errors.append({"goods_id": goods_id, "error": f"读取失败:{e}"})
                continue
            # 采集端标记需人工复核 / 验证码 / 登录页 → 不入库（真实数据也不掺假入库）
            if (cap.get("needs_review") or cap.get("is_security_verification")
                    or cap.get("is_login_page")):
                skipped += 1
                continue
            platform = str(cap.get("source_platform") or "1688")
            key = f"{platform}:{goods_id}"
            existed = bool(db.query(Product).filter(Product.dedup_key == key).first())
            try:
                # ingest_capture 幂等：已存在的商品会补建本市场本地化 Listing（多市场多站点），
                # 不重建商品、不产生脏数据；首采则新建商品 + Listing。
                ingest_capture(db, cap, market=market_upper, account_id=acc.id)
                if existed:
                    already += 1  # 商品已在库，本次补建 {market_upper} 站点的本地化 Listing
                else:
                    imported += 1
            except ValueError as e:
                skipped += 1  # 降级页/无可用SKU 拒收
                db.rollback()
            except Exception as e:
                errors.append({"goods_id": goods_id, "error": str(e)[:200]})
                db.rollback()
    finally:
        db.close()

    parts = [f"入库 {imported} 件"]
    if already:
        parts.append(f"已存在 {already} 件(补建 {market_upper} 本地化 Listing)")
    if skipped:
        parts.append(f"跳过 {skipped}")
    out = {
        "ok": True,
        "stage": "bridge_to_db",
        "imported": imported,
        "already": already,
        "skipped": skipped,
        "errors": errors,
        "run_dir": run_dir,
        "summary": "，".join(parts) + ("；入库失败 " + str(len(errors)) + " 件" if errors else ""),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
