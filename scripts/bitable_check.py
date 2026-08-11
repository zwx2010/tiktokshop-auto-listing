#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多维表格接入自检脚本 —— 一次跑通「token → 连表 → 查字段 → 核对约定」。

用法（平台根目录下）：
    python -m scripts.bitable_check

前置（open.feishu.cn 自建应用后台）：
  1. 权限管理 → 开通 bitable:app（读写多维表格）→ 版本管理 → 创建版本并发布
  2. 打开目标多维表格 → 分享/添加协作者 → 搜应用名 → 授予「可编辑」
  3. 把表格 URL 里两串填进 config/feishu.local.json：
       "bitable_app_token": ".../base/<这串>?table=..."
       "bitable_table_id":  "...?table=<这串>"
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
    print("多维表格接入自检")
    print("=" * 64)
    print(f"app_id:            {cfg.get('app_id') or '(空)'}")
    print(f"bitable_app_token: {cfg.get('bitable_app_token') or '(空)'}")
    print(f"bitable_table_id:  {cfg.get('bitable_table_id') or '(空)'}")

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
    print("自检通过 [OK]，多维表格已可读写。")
    print("下一步：发一条 @机器人 指令前，先确认表里 状态 字段取值的约定"
          "（待上架/处理中/…，见 app/feishu/bitable.py 模块头）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
