#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多维表格接入自检脚本 —— 双表: 选品采集表(表A) + 上架情况表(表B)。

用法（平台根目录下）：
    python -m scripts.bitable_check

前置（open.feishu.cn 自建应用后台）：
  1. 权限管理 → 开通 bitable:app（读写多维表格）→ 版本管理 → 创建版本并发布
  2. 打开目标多维表格 → 分享/添加协作者 → 搜应用名 → 授予「可编辑」
  3. 建两张表(可用 scripts/bitable_create_tables.py)并把 table_id 填进
     config/feishu.local.json：
       "bitable_app_token":        多维表格 URL 里 /base/<这串>
       "bitable_pick_table_id":    选品采集表(表A) 的 table_id
       "bitable_listing_table_id": 上架情况表(表B) 的 table_id
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.feishu import bitable  # noqa: E402
from app.rag.io import read_json  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "feishu.local.json"


def main() -> int:
    cfg = read_json(CONFIG_PATH)
    print("=" * 64)
    print("多维表格接入自检(双表: 选品采集表 + 上架情况表)")
    print("=" * 64)
    print(f"app_id:                   {cfg.get('app_id') or '(空)'}")
    print(f"bitable_app_token:        {cfg.get('bitable_app_token') or '(空)'}")
    print(f"bitable_pick_table_id:    {cfg.get('bitable_pick_table_id') or '(空)'}  (选品表A)")
    print(f"bitable_listing_table_id: {cfg.get('bitable_listing_table_id') or '(空)'}  (上架表B)")

    checks = bitable.self_check()
    print()
    fails = 0
    for ok, msg in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {msg}")
        if not ok:
            fails += 1
    print()
    if fails:
        print("自检未通过。按上方 FAIL 提示逐条排查，最常见三类：")
        print("  1. app_token/table_id 抄错 —— 重开表格 URL 核对")
        print("  2. 表没分享给应用 —— 表格右上角分享 → 添加协作者 → 搜应用名")
        print("  3. bitable:app 权限没生效 —— 后台开通后必须『创建版本并发布』")
        return 1
    print("自检通过 [OK]，两张表均可读写。")
    print("下一步：选品采集表(表A)由采集自动写入；上架情况表(表B)运营手动加行"
          "或 @机器人 发「耳环选3件上架到TH」生成待上架行，轮询自动跑真实流水线。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
