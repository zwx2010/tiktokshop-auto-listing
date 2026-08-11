#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 API 创建飞书多维表格两张表: 选品采集表(表A) + 上架情况表(表B)。

确定性建表(字段类型 + 单选 options 一次到位),避免手建拼错字段名/选项。
幂等:同名表已存在则跳过,不会重复建。

用法(平台根目录下):
    python -m scripts.bitable_create_tables

前置:
  1. config/feishu.local.json 已配 app_id/app_secret
  2. 应用后台开通 bitable:app 并发布
  3. 多维表格已分享给应用(可编辑)
建表完成后,把打印的 table_id 填进 config/feishu.local.json 的
  bitable_pick_table_id(表A) / bitable_listing_table_id(表B)。
"""
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.feishu import app as app_client  # noqa: E402
from app.rag.io import read_json  # noqa: E402

API = "https://open.feishu.cn/open-apis/bitable/v1"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "feishu.local.json"

# 文本=1, 数字=2, 单选=3, 日期=5
TEXT, NUMBER, SINGLE, DATE = 1, 2, 3, 5

PICK_TABLE = "选品采集表"
PICK_FIELDS = [
    {"field_name": "SPU", "type": TEXT, "description": "采集时自动赋予,唯一管理编号"},
    {"field_name": "商品ID", "type": TEXT, "description": "1688 offerId(8位以上纯数字)"},
    {"field_name": "标题(中文)", "type": TEXT},
    {"field_name": "分类", "type": SINGLE,
     "property": {"options": [{"name": c} for c in [
         "Hair Accessory", "Necklace", "Bracelet", "Earrings", "Sunglasses",
         "Hat", "Scarf", "Belt", "Ring", "Fashion Accessory"]]}},
    {"field_name": "成本价(CNY)", "type": NUMBER},
    {"field_name": "主图", "type": TEXT, "description": "图片URL"},
    {"field_name": "目标市场", "type": SINGLE,
     "property": {"options": [{"name": m} for m in ["TH", "PH", "VN"]]}},
    {"field_name": "采集时间", "type": DATE},
    {"field_name": "备注", "type": TEXT},
]

LISTING_TABLE = "上架情况表"
LISTING_FIELDS = [
    {"field_name": "SPU", "type": TEXT, "description": "关联选品表/平台库"},
    {"field_name": "商品ID", "type": TEXT},
    {"field_name": "标题(中文)", "type": TEXT},
    {"field_name": "分类", "type": SINGLE,
     "property": {"options": [{"name": c} for c in [
         "Hair Accessory", "Necklace", "Bracelet", "Earrings", "Sunglasses",
         "Hat", "Scarf", "Belt", "Ring", "Fashion Accessory"]]}},
    {"field_name": "店铺站点", "type": SINGLE,
     "property": {"options": [{"name": s} for s in ["TH店铺", "PH店铺", "VN店铺"]]}},
    {"field_name": "状态", "type": SINGLE,
     "property": {"options": [{"name": s} for s in [
         "待上架", "处理中", "文案生成中", "图片质检中", "审批中", "上架中",
         "已上架", "上架失败", "已驳回"]]}},
    {"field_name": "锁定时间", "type": DATE, "description": "拿行即翻写这里"},
    {"field_name": "上架时间", "type": DATE},
    {"field_name": "上架链接", "type": TEXT},
    {"field_name": "失败原因", "type": TEXT},
    {"field_name": "备注", "type": TEXT},
]


def _headers():
    token, err = app_client.get_tenant_access_token()
    if err:
        raise SystemExit(f"取 tenant_access_token 失败: {err}")
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"}


def _req(method, suffix, payload=None):
    cfg = read_json(CONFIG_PATH)
    app_token = cfg.get("bitable_app_token") or ""
    if not app_token:
        raise SystemExit("feishu.local.json 缺 bitable_app_token,先填")
    url = f"{API}/apps/{app_token}{suffix}"
    r = requests.request(method, url, headers=_headers(),
                         json=payload, timeout=20)
    d = r.json()
    if d.get("code") != 0:
        raise SystemExit(f"{method} {suffix} 失败: code={d.get('code')} "
                         f"msg={d.get('msg')} {str(d.get('data'))[:160]}")
    return d.get("data", {})


def _existing_tables():
    data = _req("GET", "/tables")
    return {str(t.get("name") or ""): str(t.get("table_id") or "")
            for t in (data.get("items") or [])}


def _create_table(name, fields):
    data = _req("POST", "/tables",
                payload={"table": {"name": name, "fields": fields}})
    return (data.get("table") or {}).get("table_id") or ""


def main() -> int:
    print("=" * 60)
    print("创建飞书多维表格(选品采集表 + 上架情况表)")
    print("=" * 60)
    existing = _existing_tables()
    results = {}
    for name, fields in ((PICK_TABLE, PICK_FIELDS), (LISTING_TABLE, LISTING_FIELDS)):
        if name in existing:
            print(f"  [跳过] 表「{name}」已存在: {existing[name]}")
            results[name] = existing[name]
        else:
            tid = _create_table(name, fields)
            if not tid:
                raise SystemExit(f"创建表「{name}」失败(无 table_id)")
            print(f"  [创建] 表「{name}」: {tid}")
            results[name] = tid
    print()
    print("下一步: 把下面两串填进 config/feishu.local.json")
    print(f'  "bitable_pick_table_id":     "{results[PICK_TABLE]}"   # 选品采集表(表A)')
    print(f'  "bitable_listing_table_id":  "{results[LISTING_TABLE]}"  # 上架情况表(表B)')
    print("填完跑 python -m scripts.bitable_check 自检。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
