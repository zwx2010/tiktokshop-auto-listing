"""把真实拼多多采集数据灌入平台数据库。

数据来源：../tk自动化工作流/runs/smoke_01/（项目同级的真实采集 run）
  captures/pdd_capture_*.json           —— 真实采集 JSON
  {th,ph,vn}/codex_listing_copy.csv     —— 已生成的三语言真实文案

用法：
    cd TikTokShop_Platform
    python scripts/seed_from_captures.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

# 让 `from app...` 从项目根目录可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.coze_client import DictCozeClient  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Account, Product  # noqa: E402
from app.pipeline import ingest_capture  # noqa: E402

WORKFLOW_RUNS = PROJECT_ROOT.parent / "tk自动化工作流" / "runs" / "smoke_01"
CAPTURES_DIR = WORKFLOW_RUNS / "captures"
MARKETS = ["TH", "PH", "VN"]


def _load_copy_data() -> dict:
    data: dict = {}
    for market in MARKETS:
        copy_csv = WORKFLOW_RUNS / market.lower() / "codex_listing_copy.csv"
        if not copy_csv.exists():
            continue
        with open(copy_csv, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if not row.get("goods_id"):
                    continue
                data[f"{market}|{row['goods_id']}"] = {
                    "title": row.get("title", ""),
                    "description": row.get("description", ""),
                    "keywords": row.get("keywords", ""),
                    "styles_en": [s for s in (row.get("styles_en") or "").split("|") if s],
                }
    return data


def main() -> None:
    init_db()
    copy_data = _load_copy_data()
    print(f"加载真实文案: {len(copy_data)} 条")
    captures = sorted(CAPTURES_DIR.glob("pdd_capture_*.json"))
    print(f"采集 JSON: {len(captures)} 个")

    db = SessionLocal()
    try:
        for market in MARKETS:
            acc = db.query(Account).filter(Account.name == f"RoseSeek_{market}").first()
            if not acc:
                acc = Account(name=f"RoseSeek_{market}", market_code=market,
                              store_name="RoseSeek", status="active")
                db.add(acc)
                db.flush()
            coze = DictCozeClient(copy_data)
            ok = skip = 0
            for path in captures:
                capture = json.loads(path.read_text(encoding="utf-8-sig"))
                if not capture.get("goods_id") or not capture.get("title"):
                    skip += 1
                    continue
                if not capture.get("sku_variants") and not float(capture.get("price_cny") or 0):
                    skip += 1
                    continue
                ingest_capture(db, capture, market=market, account_id=acc.id, coze=coze)
                ok += 1
            db.commit()
            print(f"[{market}] 入库商品 {ok} 个（跳过 {skip} 个无效采集）")
    finally:
        db.close()

    db = SessionLocal()
    try:
        total = db.query(Product).count()
    finally:
        db.close()
    print(f"\n完成！数据库商品总数: {total}")
    print("启动看板: python -m uvicorn app.main:app --port 8000  →  http://127.0.0.1:8000/dashboard")


if __name__ == "__main__":
    main()
