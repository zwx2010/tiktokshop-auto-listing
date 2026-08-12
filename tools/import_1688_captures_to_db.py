#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""1688 采集产物 → 平台库 platform.db 桥接脚本（采集入库功能核心）。

从 1688 CDP 采集 run 目录读逐件 capture JSON（captures/1688_capture_*.json，UTF-8-sig），
调用 app.pipeline.ingest_capture 写入平台库。幂等：dedup_key = {source_platform}:{goods_id}
（1688:...），重复灌入不产生脏数据，已存在的商品记为 already 跳过。

用法：
    python tools/import_1688_captures_to_db.py --run-dir <runs/1688_xxx> --market th
    python tools/import_1688_captures_to_db.py --auto-latest --market ph   # 最新一个 1688 run
    python tools/import_1688_captures_to_db.py --min-cost 2 --max-cost 40  # 覆盖成本区间

设计：
- 确定性脚本，不经 LLM —— DB 写入是源头事实，结果如实打印，由编排层（approval.py）解析；
- 采集端标记 needs_review / 验证码 / 登录页 的件直接跳过，不入库；
- 成本区间过滤：成本价不在 [min, max] 区间的件不入库、不写选品表，单独计入 cost_filtered。
  口径与选品表「成本价(CNY)」完全一致（app.cleaning.select_cost，SKU 矩阵最低价→兜底详情价）；
  区间默认读 config/listing_defaults.json 的 capture_cost_bounds，--min-cost/--max-cost 可覆盖，
  传 0 表示不设该侧边界（0 价/未知成本的件始终跳过，不掺假入库）；
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

from app import cleaning  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.feishu import bitable  # noqa: E402
from app.models import Account, Product  # noqa: E402
from app.pipeline import ingest_capture  # noqa: E402


def _sync_pick_table(product) -> None:
    """新入库商品写一行到飞书选品采集表(表A)。

    幂等(按商品ID查重);失败只记日志不中断采集——选品表是展示层,
    平台库才是源头事实。缺飞书配置时 bitable 抛 BitableUnconfigured,同样吞掉。
    """
    try:
        from datetime import timezone
        created = product.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        result = bitable.pick_upsert({
            "SPU": product.spu,
            "商品ID": product.source_goods_id,
            "标题(中文)": (product.title_cn or "")[:200],
            "分类": product.category,
            "成本价(CNY)": float(product.cost_cny_used or 0),
            "主图": product.main_image_url or "",
            "目标市场": (product.market_code or "TH").upper(),
            "采集时间": int(created.timestamp() * 1000) if created else None,
        })
        if result == "created":
            print(f"[bitable] 选品表新增 {product.spu} "
                  f"{str(product.title_cn)[:20]}", flush=True)
    except Exception as exc:
        print(f"[bitable] 选品表同步失败({product.spu}): {exc}", flush=True)

# 与 app/agent/tasks.py 同源：ROSEEK_PKG_DIR 可覆盖，缺省用项目根的相对路径
_PKG = os.environ.get(
    "ROSEEK_PKG_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "RoseSeek_TikTokShop_AI_Localized_20260809"),
)


def _cost_bounds(min_cost: float | None, max_cost: float | None) -> tuple[float, float]:
    """成本区间 (lo, hi)。CLI 参数优先；缺省读 config/listing_defaults.json 的
    capture_cost_bounds。0 或 None = 不设该侧边界（只挡下限/只挡上限/全放行）。
    """
    lo = hi = 0.0
    try:
        from app.config import CONFIG_DIR
        cfg = json.loads((CONFIG_DIR / "listing_defaults.json").read_text(encoding="utf-8"))
        bounds = cfg.get("capture_cost_bounds") or {}
        lo = float(bounds.get("min") or 0)
        hi = float(bounds.get("max") or 0)
    except Exception:
        pass  # 读不到配置就全放行,不因配置异常卡死采集
    if min_cost is not None:
        lo = float(min_cost or 0)
    if max_cost is not None:
        hi = float(max_cost or 0)
    return lo, hi


def _bounds_text(lo: float, hi: float) -> str:
    if lo > 0 and hi > 0:
        return f"{lo:g}~{hi:g}元"
    if lo > 0:
        return f">={lo:g}元"
    if hi > 0:
        return f"<={hi:g}元"
    return "不限"


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
    ap.add_argument("--min-cost", type=float, default=None,
                    help="成本价下限(含)，默认读 config/listing_defaults.json 的 capture_cost_bounds；0=不设")
    ap.add_argument("--max-cost", type=float, default=None,
                    help="成本价上限(含)，默认读 config/listing_defaults.json 的 capture_cost_bounds；0=不设")
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
    lo, hi = _cost_bounds(args.min_cost, args.max_cost)
    print(f"[import] 成本区间 {_bounds_text(lo, hi)}", flush=True)
    imported = already = skipped = cost_filtered = 0
    cost_filtered_detail = []
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
            # 成本区间过滤：不入库、不写选品表。口径 = select_cost(与选品表「成本价(CNY)」一致)，
            # 0 价/未知成本也跳过 —— 成本是选品下限，缺失不猜不凑。
            cost = cleaning.select_cost(cap)["cost_cny"]
            if (lo > 0 and cost < lo) or (hi > 0 and cost > hi) or cost <= 0:
                cost_filtered += 1
                cost_filtered_detail.append(
                    {"goods_id": goods_id, "cost_cny": round(float(cost), 2)})
                continue
            platform = str(cap.get("source_platform") or "1688")
            key = f"{platform}:{goods_id}"
            existed = bool(db.query(Product).filter(Product.dedup_key == key).first())
            try:
                # ingest_capture 幂等：已存在的商品会补建本市场本地化 Listing（多市场多站点），
                # 不重建商品、不产生脏数据；首采则新建商品 + Listing。
                product = ingest_capture(db, cap, market=market_upper,
                                         account_id=acc.id)
                if existed:
                    already += 1  # 商品已在库，本次补建 {market_upper} 站点的本地化 Listing
                else:
                    imported += 1
                    # 新入库 → 自动写一行到飞书选品表(表A),幂等
                    _sync_pick_table(product)
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
    if cost_filtered:
        parts.append(f"成本过滤 {cost_filtered} 件(区间 {_bounds_text(lo, hi)})")
    out = {
        "ok": True,
        "stage": "bridge_to_db",
        "imported": imported,
        "already": already,
        "skipped": skipped,
        "cost_filtered": cost_filtered,
        "cost_bounds": {"min": lo, "max": hi},
        "cost_filtered_detail": cost_filtered_detail[:20],
        "errors": errors,
        "run_dir": run_dir,
        "summary": "，".join(parts) + ("；入库失败 " + str(len(errors)) + " 件" if errors else ""),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
