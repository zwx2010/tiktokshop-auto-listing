"""飞书多维表格客户端 —— 双表: 选品采集表 + 上架情况表。

配置(config/feishu.local.json)三个键:
    "bitable_app_token":        多维表格 URL 里 /base/<这串> 的 app_token
    "bitable_pick_table_id":    表A 选品采集表(采集商品,一行一件,带 SPU)
    "bitable_listing_table_id": 表B 上架情况表(一行 = SPU×店铺站点的上架任务)
缺配置时所有业务函数抛 BitableUnconfigured，编排层据此优雅降级
（轮询不启动、不碰表格），现有飞书消息流程完全不受影响。

前置条件（见 scripts/bitable_check.py 自检）：
  1. 应用后台开通 bitable:app 并发布新版本
  2. 多维表格「分享 → 添加协作者 → 搜应用名 → 可编辑」
  3. 字段与状态机约定对齐（脚本自检会列出缺哪些字段）

业务约定：
  表A 选品采集:一行一件采集商品,SPU 采集时自动赋予,纯展示。
  表B 上架情况:以店铺视角,一行 = 某 SPU 在某店铺站点的上架任务。
    状态字段单选取值：待上架 / 处理中 / 文案生成中 / 图片质检中 / 审批中 /
                      上架中 / 已上架 / 上架失败 / 已驳回
    拿行即翻：轮询读到「待上架」行，立刻整批改成「处理中」+ 写「锁定时间」，
             下次轮询按「待上架」查就查不到了 → 天然防重复。
    超时回滚：状态停在处理中/文案生成中/…/上架中 且 锁定时间 超过 30 分钟 →
             视为进程崩过的残留锁，重置回「待上架」。

所有写表函数默认指向上架表(表B);采集同步单独走 pick_* 写选品表(表A)。
"""
import time

import requests

from ..config import CONFIG_DIR
from ..rag.io import read_json
from . import app as app_client

API = "https://open.feishu.cn/open-apis/bitable/v1"

# 上架表「状态」单选取值 —— 与 app/agent/bitable_flow.py 常量一致
LISTING_STATUSES = ["待上架", "处理中", "文案生成中", "图片质检中",
                    "审批中", "上架中", "已上架", "上架失败", "已驳回"]
# 上架表「店铺站点」单选取值
SITE_OPTIONS = ["TH店铺", "PH店铺", "VN店铺"]
# 选品表「分类」单选取值 —— 与 app/cleaning.infer_category 产出对齐
CATEGORY_OPTIONS = ["Hair Accessory", "Necklace", "Bracelet", "Earrings",
                    "Sunglasses", "Hat", "Scarf", "Belt", "Ring",
                    "Fashion Accessory"]


class BitableError(Exception):
    """连通/权限/参数错误，编排层 catch 后如实记录，不影响其他流程。"""


class BitableUnconfigured(BitableError):
    """feishu.local.json 没配 bitable_app_token / pick / listing table_id。"""


def _cfg():
    p = CONFIG_DIR / "feishu.local.json"
    return read_json(p) if p.is_file() else {}


def is_configured() -> bool:
    c = _cfg()
    return bool(c.get("bitable_app_token")
                and c.get("bitable_pick_table_id")
                and c.get("bitable_listing_table_id"))


def _tokens():
    """取 app_token + 两张表的 table_id。缺任一 → BitableUnconfigured。"""
    c = _cfg()
    app_token = c.get("bitable_app_token") or ""
    pick_tid = c.get("bitable_pick_table_id") or ""
    listing_tid = c.get("bitable_listing_table_id") or ""
    if not (app_token and pick_tid and listing_tid):
        raise BitableUnconfigured(
            "多维表格未配置：feishu.local.json 缺 bitable_app_token / "
            "bitable_pick_table_id / bitable_listing_table_id")
    return app_token, pick_tid, listing_tid


def _headers():
    token, err = app_client.get_tenant_access_token()
    if err:
        raise BitableError(f"取 tenant_access_token 失败: {err}")
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"}


def _request(method, suffix, *, payload=None, timeout=15, table_id=None):
    """发请求并解封套。table_id 缺省指向上架表(表B)。非 0 code 抛 BitableError。"""
    app_token, _pick_tid, listing_tid = _tokens()
    tid = table_id or listing_tid
    url = f"{API}/apps/{app_token}/tables/{tid}{suffix}"
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
    """单个筛选条件 → 飞书 filter 结构。仅支持单层条件:
    飞书 search 的 conjunction 只能在顶层,嵌套 or/and 分组不被接受
    (实测 code=99992402 field validation failed)。
    """
    if isinstance(item, dict):
        raise BitableError("飞书 search 不支持嵌套 filter(conjunction 只能顶层单层),"
                           "请平铺条件,或多次查询后本地合并")
    fn, op, v = item
    return {"field_name": fn, "operator": op,
            "value": [v] if isinstance(v, str) else v}


def _normalize_fields(fields):
    """把飞书读回的字段值归一化:文本字段常以分段数组返回
    [{text, type}, ...](一行一段),统一摊平回纯字符串;单选/数字/日期原样保留。
    若不做这步,`SPU` 读到的是 "[{'text': 'SPU000002', 'type': 'text'}]",
    跟平台库字符串 'SPU000002' 比对永远不相等(实测整批标上架失败)。
    """
    if not isinstance(fields, dict):
        return fields
    out = {}
    for k, v in fields.items():
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v) \
                and any("text" in x for x in v):
            out[k] = "".join(str(x.get("text") or "") for x in v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- 上架表(表B)
def list_records(filters, conjunction="and", page_size=100, table_id=None):
    """搜索记录（默认上架表）。filters: [(field_name, operator, value), ...],
    顶层按 conjunction 组合。注意:飞书 search 只支持单层条件,嵌套 or/and 分组
    会被拒(99992402),「某字段∈多值 且 其他条件」请先平铺查一层再本地过滤。
    日期范围比较 value 用 ["ExactDate", "毫秒时间戳"]。
    返回 [{record_id, fields, create_time, ...}]。"""
    # 注意:不传 sort —— 飞书 search 的 sort.field_name 必须是表内真实字段名,
    # 而「创建时间」不是表字段(API 建的表没有它),传了会 InvalidSort。
    payload = {"page_size": page_size}
    if filters:
        payload["filter"] = {"conjunction": conjunction,
                             "conditions": [_condition_item(c) for c in filters]}
    data = _request("POST", "/records/search", payload=payload, table_id=table_id)
    return [dict(r, fields=_normalize_fields(r.get("fields") or {}))
            for r in data.get("items", [])]


def batch_update(records, table_id=None):
    """records: [{record_id, fields}] 批量更新字段（默认上架表）。空列表直接返回。"""
    if not records:
        return {}
    return _request("POST", "/records/batch_update",
                    payload={"records": records}, table_id=table_id)


def create_record(fields, table_id=None):
    """新增一行（默认上架表），返回 record_id。"""
    data = _request("POST", "/records", payload={"fields": fields},
                    table_id=table_id)
    return (data.get("record") or {}).get("record_id")


def update_record(record_id, fields, table_id=None):
    """更新单行字段（默认上架表）。"""
    return _request("PUT", f"/records/{record_id}", payload={"fields": fields},
                    table_id=table_id)


def delete_record(record_id, table_id=None):
    """删除一行（默认上架表）。幂等:记录不存在会抛 BitableError,调用方自行决定是否吞。"""
    return _request("DELETE", f"/records/{record_id}", table_id=table_id)


def list_fields(table_id=None):
    """返回 [{field_name, type, ...}]（默认上架表），用于自检字段是否齐全。"""
    return _request("GET", "/fields", table_id=table_id).get("items", [])


def pickup_pending(lock_from="待上架", lock_to="处理中", page_size=20,
                   lock_field="锁定时间"):
    """拿行即翻（上架表）：读 状态=lock_from 的行，立刻整批翻成 lock_to 并写 lock_field。

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


def stale_locked(lock_statuses, stale_ms, lock_field="锁定时间", status_field="状态"):
    """找锁定超时的残留行（上架表）。进程崩溃会留下永远停在「处理中/…/上架中」
    的行，轮询查「待上架」永远碰不到，必须显式回滚，否则整池货静默不动。

    飞书 search 不支持嵌套 or 分组(「状态∈多值」+「锁定时间<截止」无法在服务端
    单次表达),所以先按日期单条件查超时行,状态过滤在本地做。
    """
    if not lock_statuses:
        return []
    cutoff = int(time.time() * 1000) - stale_ms
    rows = list_records([(lock_field, "isLess", ["ExactDate", str(cutoff)])])
    return [r for r in rows
            if (r.get("fields") or {}).get(status_field) in lock_statuses]


# ---------------------------------------------------------------- 选品表(表A)
def find_pick_by_goods_id(goods_id):
    """按 商品ID 查选品表(表A)已有行,没有返回 None。用于采集同步幂等。"""
    if not goods_id:
        return None
    _app_token, pick_tid, _listing_tid = _tokens()
    rows = list_records([("商品ID", "is", str(goods_id))], table_id=pick_tid)
    return rows[0] if rows else None


def pick_upsert(fields):
    """采集入库后把商品写入选品表(表A),按 商品ID 幂等:已存在跳过,不存在 create。

    fields 至少含 商品ID/SPU/标题(中文)/分类/成本价(CNY)/主图/目标市场/采集时间。
    返回 "exists"(跳过) 或 "created"(新增)。
    """
    gid = str(fields.get("商品ID") or "")
    if not gid:
        return "exists"
    app_token, pick_tid, _listing_tid = _tokens()
    rows = list_records([("商品ID", "is", gid)], table_id=pick_tid)
    if rows:
        return "exists"
    create_record(fields, table_id=pick_tid)
    return "created"


# ---------------------------------------------------------------- 自检
def self_check():
    """连通性 + 双表字段契约自检。返回 [(ok: bool, msg: str), ...]。"""
    out = []
    c = _cfg()
    if not (c.get("bitable_app_token")
            and c.get("bitable_pick_table_id")
            and c.get("bitable_listing_table_id")):
        out.append((False, "feishu.local.json 缺 bitable_app_token / "
                           "bitable_pick_table_id / bitable_listing_table_id"))
        out.append((False, "先用 scripts/bitable_create_tables.py 建两张表,把打印的 "
                           "table_id 填进配置再自检"))
        return out
    try:
        app_token, pick_tid, listing_tid = _tokens()
    except BitableUnconfigured as exc:
        out.append((False, str(exc)))
        return out

    # 上架表(表B)
    out.append((True, "-- 上架情况表(表B) --"))
    _check_table_fields(out, listing_tid, [
        "SPU", "商品ID", "标题(中文)", "分类", "成本价(CNY)", "店铺站点", "状态",
        "锁定时间", "上架时间", "上架链接", "失败原因", "备注",
    ])
    _check_single_select(out, listing_tid, "状态", LISTING_STATUSES)
    _check_single_select(out, listing_tid, "店铺站点", SITE_OPTIONS)

    # 选品表(表A)
    out.append((True, "-- 选品采集表(表A) --"))
    _check_table_fields(out, pick_tid, [
        "SPU", "商品ID", "标题(中文)", "分类", "成本价(CNY)", "主图",
        "目标市场", "采集时间", "备注",
    ])
    _check_single_select(out, pick_tid, "分类", CATEGORY_OPTIONS)
    _check_single_select(out, pick_tid, "目标市场", ["TH", "PH", "VN"])
    return out


def _check_table_fields(out, table_id, need):
    """检查某表字段是否齐全。缺字段追加 (False, 提示)。"""
    try:
        names = [str(f.get("field_name") or "")
                 for f in list_fields(table_id=table_id)]
    except BitableError as exc:
        out.append((False, f"表 {table_id} 连不上: {exc}"))
        return
    out.append((True, f"表可连，共 {len(names)} 个字段"))
    for n in need:
        out.append((n in names, f"字段「{n}」" + ("存在" if n in names else "缺失")))


def _check_single_select(out, table_id, field_name, expected):
    """检查单选取值是否与约定一致。缺选项 → (False, 列出差集)。"""
    try:
        for f in list_fields(table_id=table_id):
            if str(f.get("field_name") or "") != field_name:
                continue
            opts = [str(o.get("name") or "") for o in
                    ((f.get("property") or {}).get("options") or [])]
            missing = [x for x in expected if x not in opts]
            if missing:
                out.append((False, f"「{field_name}」单选缺选项: {missing}"))
            else:
                out.append((True, f"「{field_name}」单选选项与约定一致 "
                                  f"({len(opts)}个)"))
            return
        out.append((False, f"字段「{field_name}」不存在，无法核对单选"))
    except BitableError as exc:
        out.append((False, f"核对「{field_name}」选项失败: {exc}"))
