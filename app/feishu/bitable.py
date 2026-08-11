"""飞书多维表格客户端 —— 选品池读写 / 拿行即翻 / 状态与结果回写。

配置(config/feishu.local.json)新增两个键：
    "bitable_app_token": 多维表格 URL 里 /base/<这串> 的 app_token
    "bitable_table_id":  表 ID(URL 里 table=<table_id>)
缺配置时所有业务函数抛 BitableUnconfigured，编排层据此优雅降级
（轮询不启动、不碰表格），现有飞书消息流程完全不受影响。

前置条件（见 scripts/bitable_check.py 自检）：
  1. 应用后台开通 bitable:app 并发布新版本
  2. 多维表格「分享 → 添加协作者 → 搜应用名 → 可编辑」
  3. 字段与状态机约定对齐（脚本自检会列出缺哪些字段）

业务约定（选品池一张表，一行一个商品）：
  状态字段单选取值：待上架 / 处理中 / 文案生成中 / 图片质检中 / 审批中 /
                    上架中 / 已上架 / 上架失败 / 已驳回
  拿行即翻：轮询读到「待上架」行，立刻整批改成「处理中」+ 写「锁定时间」，
           下次轮询按「待上架」查就查不到了 → 天然防重复。
  超时回滚：状态停在处理中/文案生成中/…/上架中 且 锁定时间 超过 30 分钟 →
           视为进程崩过的残留锁，重置回「待上架」。
"""
import time

import requests

from ..config import CONFIG_DIR
from ..rag.io import read_json
from . import app as app_client

API = "https://open.feishu.cn/open-apis/bitable/v1"


class BitableError(Exception):
    """连通/权限/参数错误，编排层 catch 后如实记录，不影响其他流程。"""


class BitableUnconfigured(BitableError):
    """feishu.local.json 没配 bitable_app_token / bitable_table_id。"""


def _cfg():
    p = CONFIG_DIR / "feishu.local.json"
    return read_json(p) if p.is_file() else {}


def is_configured() -> bool:
    c = _cfg()
    return bool(c.get("bitable_app_token") and c.get("bitable_table_id"))


def _tokens():
    c = _cfg()
    app_token = c.get("bitable_app_token") or ""
    table_id = c.get("bitable_table_id") or ""
    if not (app_token and table_id):
        raise BitableUnconfigured(
            "多维表格未配置：feishu.local.json 缺 bitable_app_token / bitable_table_id")
    return app_token, table_id


def _headers():
    token, err = app_client.get_tenant_access_token()
    if err:
        raise BitableError(f"取 tenant_access_token 失败: {err}")
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"}


def _request(method, suffix, *, payload=None, timeout=15):
    """发请求并解封套。非 0 code 一律抛 BitableError（带 code/msg/摘要），不静默。"""
    app_token, table_id = _tokens()
    url = f"{API}/apps/{app_token}/tables/{table_id}{suffix}"
    try:
        r = requests.request(method, url, headers=_headers(),
                             json=payload, timeout=timeout)
        d = r.json()
    except Exception as exc:
        raise BitableError(f"{method} {suffix} 请求异常: {exc}") from exc
    if d.get("code") != 0:
        data = d.get("data") or {}
        raise BitableError(
            f"{method} {suffix} 失败: code={d.get('code')} msg={d.get('msg')} "
            f"{str(data)[:160]}")
    return d.get("data", {})


def _condition_item(item):
    """单个筛选条件 → 飞书 filter 结构。支持嵌套（dict 传子 filter 实现 or 分组）。"""
    if isinstance(item, dict):
        return {"conjunction": item.get("conjunction", "and"),
                "conditions": [_condition_item(c) for c in item.get("conditions", [])]}
    fn, op, v = item
    return {"field_name": fn, "operator": op,
            "value": [v] if isinstance(v, str) else v}


def list_records(filters, conjunction="and", page_size=100):
    """搜索记录。filters 示例:
        [("状态", "is", "待上架"), ("目标市场", "is", "TH")]
        [({"conjunction": "or",
           "conditions": [("状态", "is", "处理中"), ("状态", "is", "上架中")]},
          ("锁定时间", "isLess", 1700000000000))]
    返回 [{record_id, fields, create_time, ...}]。"""
    payload = {"page_size": page_size,
               "sort": [{"field_name": "创建时间", "desc": False}]}
    if filters:
        payload["filter"] = {"conjunction": conjunction,
                             "conditions": [_condition_item(c) for c in filters]}
    data = _request("POST", "/records/search", payload=payload)
    return data.get("items", [])


def batch_update(records):
    """records: [{record_id, fields}] 批量更新字段。空列表直接返回。"""
    if not records:
        return {}
    return _request("POST", "/records/batch_update",
                    payload={"records": records})


def create_record(fields):
    """新增一行，返回 record_id。"""
    data = _request("POST", "/records", payload={"fields": fields})
    return (data.get("record") or {}).get("record_id")


def update_record(record_id, fields):
    """更新单行字段。"""
    return _request("PUT", f"/records/{record_id}", payload={"fields": fields})


def list_fields():
    """返回 [{field_name, type, ...}]，用于自检字段是否齐全。"""
    return _request("GET", "/fields").get("items", [])


# ---------------------------------------------------------------- 拿行即翻
def pickup_pending(lock_from="待上架", lock_to="处理中", page_size=20,
                   lock_field="锁定时间"):
    """拿行即翻：读 状态=lock_from 的行，立刻整批翻成 lock_to 并写 lock_field。

    翻锁必须在任何慢操作（制表/审图/上架）之前完成 —— 翻完锁，下次轮询按
    lock_from 查就查不到，天然防重复。返回翻完锁的行列表。
    """
    rows = list_records([("状态", "is", lock_from)], page_size=page_size)
    if not rows:
        return []
    now_ms = int(time.time() * 1000)
    batch_update([{"record_id": r["record_id"],
                   "fields": {"状态": lock_to, lock_field: now_ms}}
                  for r in rows])
    print(f"[bitable] 拿行即翻 {len(rows)} 行: {lock_from} → {lock_to}", flush=True)
    return rows


def stale_locked(lock_statuses, stale_ms, lock_field="锁定时间"):
    """找锁定超时的残留行（状态在 lock_statuses 且 锁定时间 距今 > stale_ms）。

    进程崩溃会留下永远停在「处理中/…/上架中」的行，轮询查「待上架」永远碰不到，
    必须显式回滚，否则整池货静默不动。返回这些行。
    """
    if not lock_statuses:
        return []
    return list_records([
        {"conjunction": "or",
         "conditions": [("状态", "is", st) for st in lock_statuses]},
        (lock_field, "isLess", int(time.time() * 1000) - stale_ms),
    ])


# ---------------------------------------------------------------- 自检
def self_check():
    """连通性 + 字段约定自检。返回 [(ok: bool, msg: str), ...]。
    任何异常都吞成 (False, 排查线索)，不自检崩溃。"""
    out = []
    c = _cfg()
    if not (c.get("bitable_app_token") and c.get("bitable_table_id")):
        out.append((False, "feishu.local.json 缺 bitable_app_token / bitable_table_id"))
        out.append((False, "在飞书打开多维表格，URL 里 /base/<app_token>?table=<table_id> 两串填进配置"))
        return out
    try:
        fields = list_fields()
        out.append((True, f"表可连，共 {len(fields)} 个字段"))
        names = [str(f.get("field_name") or "") for f in fields]
        need = ["商品ID", "标题(中文)", "分类", "目标市场", "成本价(CNY)",
                "状态", "锁定时间", "备注"]
        for n in need:
            out.append((n in names, f"字段「{n}」" + ("存在" if n in names else "缺失")))
        if "状态" in names:
            out.append((True, "状态字段已配（约定取值见 app/feishu/bitable.py 模块头）"))
    except BitableError as exc:
        out.append((False, str(exc)))
        out.append((False, "排查：表是否分享给应用？bitable:app 权限是否已发布？app_token/table_id 是否抄对？"))
    return out
