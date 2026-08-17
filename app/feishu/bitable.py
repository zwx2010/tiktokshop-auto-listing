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
import threading
import time

import requests

from ..config import CONFIG_DIR
from ..rag.io import read_json
from . import app as app_client

API = "https://open.feishu.cn/open-apis/bitable/v1"

# 上架表「状态」单选取值 —— 与 app/agent/bitable_flow.py 常量一致
# 「上架待核对」= 批次级结果含失败件、无法逐件定位时的诚实中间态
# (卖家后台只给成功/失败件数,不标成功也不标失败,提示运营核对)。
LISTING_STATUSES = ["待上架", "处理中", "文案生成中", "图片质检中",
                    "审批中", "上架中", "已上架", "上架失败", "已驳回",
                    "上架待核对"]
# 上架表「店铺站点」单选取值
SITE_OPTIONS = ["TH店铺", "PH店铺", "VN店铺"]
# 选品表「分类」单选取值 —— 与 app/cleaning.infer_category 产出对齐
CATEGORY_OPTIONS = ["Hair Accessory", "Necklace", "Bracelet", "Earrings",
                    "Sunglasses", "Hat", "Scarf", "Belt", "Ring",
                    "Fashion Accessory"]

# 展示列: 上架表(表B) 图1~图9 附件字段(一列一张,商品图最多 9 张) + SKU明细;
# 选品表(表A) 主图图片 附件字段 + SKU明细。图片列值 = [{"file_token": ...}],
# 不能直接填外部 URL(实测 AttachFieldConvFail),必须先下载→上传飞书拿 token。
IMAGE_COLUMNS = [f"图{i}" for i in range(1, 10)]
SKU_DETAIL_FIELD = "SKU明细"
PICK_MAIN_IMAGE_FIELD = "主图图片"
# select_upload 新建行写入批次号(如 sel_20260813_153000),轮询按批处理,
# 避免把表里残留的旧「待上架」行整批卷进来混批。
BATCH_FIELD = "任务批次"

# 进程内 URL→file_token 缓存: 同商品多站点行 / 多次操作复用,避免重复上传
_IMAGE_TOKEN_CACHE: dict[str, str] = {}
_IMAGE_LOCK = threading.Lock()


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


def ensure_status_option(status, table_id=None):
    """确保上架表「状态」单选字段含某选项(幂等)。

    Feishu 单选字段写入不在 options 里的值会被拒(实测),新增状态须先同步字段
    的 property.options。用于「上架待核对」等诚实中间态。返回是否成功(已存在也 True)。
    """
    try:
        tid = table_id
        fields = list_fields(table_id=tid)
        sf = next((f for f in fields if str(f.get("field_name") or "") == "状态"), None)
        if not sf:
            print(f"[bitable] 状态字段未找到,无法建选项 {status!r}", flush=True)
            return False
        opts = [(sf.get("property") or {}).get("options") or []]
        names = {str(o.get("name") or "") for o in opts[0]}
        if status in names:
            return True
        new_opts = [{"name": n} for n in LISTING_STATUSES]
        field_id = sf.get("field_id")
        _request("PUT", f"/fields/{field_id}",
                 payload={"field_name": sf.get("field_name"),
                          "type": sf.get("type"),
                          "property": {"options": new_opts}}, table_id=tid)
        print(f"[bitable] 状态字段已加选项 {status!r}", flush=True)
        return True
    except Exception as exc:
        print(f"[bitable] 建状态选项失败({status!r}): {exc}", flush=True)
        return False


def delete_record(record_id, table_id=None):
    """删除一行（默认上架表）。幂等:记录不存在会抛 BitableError,调用方自行决定是否吞。"""
    return _request("DELETE", f"/records/{record_id}", table_id=table_id)


def list_fields(table_id=None):
    """返回 [{field_name, type, ...}]（默认上架表），用于自检字段是否齐全。"""
    return _request("GET", "/fields", table_id=table_id).get("items", [])


# 新建批次冷却:select_upload/补位建行是逐个 API 调用(10 行约 20~30s,网络慢更久),
# 轮询每 45s 捡一次,若撞在建行中途会把同一批拦腰捡走(实测 10 行被捡 7 行剩 3 行,
# 剩余行要等整批跑完才被捡,批次被拆两段)。冷却期内不捡新批次,等建完再整批进流水线。
_BATCH_COOLDOWN_S = 45


def _batch_started_at(batch_id) -> float | None:
    """从批次号解析建行起点时间戳(s)。sel_/refill_YYYYMMDD_HHMMSS。
    解析不了(手动/无批次)返回 None,不设冷却。"""
    b = str(batch_id or "").strip()
    for prefix in ("sel_", "refill_"):
        if b.startswith(prefix):
            try:
                return time.mktime(time.strptime(b[len(prefix):], "%Y%m%d_%H%M%S"))
            except ValueError:
                return None
    return None


def _batch_too_new(batch_id) -> bool:
    t0 = _batch_started_at(batch_id)
    if t0 is None:
        return False
    return (time.time() - t0) < _BATCH_COOLDOWN_S


def pickup_pending(lock_from="待上架", lock_to="处理中", page_size=20,
                   lock_field="锁定时间"):
    """拿行即翻（上架表）：读 状态=lock_from 的行，立刻整批翻成 lock_to 并写 lock_field。

    翻锁必须在任何慢操作（制表/审图/上架）之前完成 —— 翻完锁，下次轮询按
    lock_from 查就查不到，天然防重复。返回翻完锁的行列表。

    批次隔离:select_upload 新建行带「任务批次」;若表里存在带任务批次的待上架
    行,只捡其中批次号最新的那批(即刚 select 的行),不把表里残留的旧待上架行
    整批卷进来混批。表里手动标「待上架」的行(无任务批次)仍会正常被捡。
    新建批次在冷却期内不捡(见 _BATCH_COOLDOWN_S),防建行中途被拦腰捡成两段。
    """
    rows = list_records([("状态", "is", lock_from)], page_size=page_size)
    if not rows:
        return []
    batched = [r for r in rows
               if str((r.get("fields") or {}).get(BATCH_FIELD) or "").strip()]
    if batched:
        # 只捡最新批次(任务批次按时间字符串排序,最新最大)
        latest = max(str((r.get("fields") or {}).get(BATCH_FIELD) or "").strip()
                     for r in batched)
        if _batch_too_new(latest):
            # 批次还在建行中(逐个 API 调用,几十秒级):本次不捡,等下一轮建完。
            print(f"[bitable] 批次 {latest} 刚生成(<{_BATCH_COOLDOWN_S}s),"
                  f"本次不捡,等建完再整批进流水线", flush=True)
            return []
        rows = [r for r in rows
                if str((r.get("fields") or {}).get(BATCH_FIELD) or "").strip() == latest]
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
    返回 新建行的 record_id(新增);已存在/无商品ID 返回 None —— 调用方据此
    补写 主图图片/SKU明细(展示层,失败不影响采集主流程)。
    """
    gid = str(fields.get("商品ID") or "")
    if not gid:
        return None
    app_token, pick_tid, _listing_tid = _tokens()
    rows = list_records([("商品ID", "is", gid)], table_id=pick_tid)
    if rows:
        return None
    return create_record(fields, table_id=pick_tid)


def delete_pick_by_goods_id(goods_id):
    """按 商品ID 删除选品表(表A)行 —— 删除商品的连带动作,表A 不再展示。
    不存在静默跳过。返回实际删除行数。"""
    gid = str(goods_id or "").strip()
    if not gid:
        return 0
    app_token, pick_tid, _listing_tid = _tokens()
    rows = list_records([("商品ID", "is", gid)], table_id=pick_tid)
    deleted = 0
    for r in rows:
        try:
            delete_record(r["record_id"], table_id=pick_tid)
            deleted += 1
        except Exception as exc:
            print(f"[bitable] 删选品表行失败(gid={gid}): {exc}", flush=True)
    return deleted


def mark_listing_pending_failed(spu, reason):
    """删除商品的连带动作:把上架表(表B)中该 SPU 的「待上架」任务标「上架失败」。
    在跑的中间态行由流水线 active 过滤在处理时如实标失败。返回标记行数。"""
    spu = str(spu or "").strip()
    if not spu:
        return 0
    rows = list_records([("SPU", "is", spu), ("状态", "is", "待上架")])
    if not rows:
        return 0
    try:
        batch_update([{"record_id": r["record_id"],
                       "fields": {"状态": "上架失败", "失败原因": reason}}
                      for r in rows])
    except Exception as exc:
        print(f"[bitable] 标上架失败失败(spu={spu}): {exc}", flush=True)
        return 0
    return len(rows)


# ---------------------------------------------------------------- 图片 / SKU 展示列
def _ensure_columns(table_id, specs):
    """幂等建字段: 查已有 field_name,缺的才创建。返回新建字段数。"""
    existing = {str(f.get("field_name") or "") for f in list_fields(table_id=table_id)}
    created = 0
    for spec in specs:
        name = spec["field_name"]
        if name in existing:
            continue
        try:
            _request("POST", "/fields", payload=spec, table_id=table_id)
            created += 1
        except Exception as exc:
            print(f"[bitable] 建字段失败({name}): {exc}", flush=True)
    if created:
        print(f"[bitable] 表 {table_id} 新建 {created} 个字段", flush=True)
    return created


def ensure_listing_image_columns():
    """上架表(表B)建 图1~图9 附件字段 + SKU明细 文本字段 + 任务批次(幂等)。"""
    _app_token, _pick_tid, listing_tid = _tokens()
    return _ensure_columns(listing_tid,
                           [{"field_name": n, "type": 17} for n in IMAGE_COLUMNS]
                           + [{"field_name": SKU_DETAIL_FIELD, "type": 1},
                              {"field_name": BATCH_FIELD, "type": 1}])


def ensure_pick_visual_columns():
    """选品表(表A)建 主图图片 附件字段 + SKU明细 文本字段(幂等)。"""
    _app_token, pick_tid, _listing_tid = _tokens()
    return _ensure_columns(pick_tid, [
        {"field_name": PICK_MAIN_IMAGE_FIELD, "type": 17},
        {"field_name": SKU_DETAIL_FIELD, "type": 1},
    ])


def _image_filename(url):
    """从 URL 提取带扩展名的文件名;取不到则兜底 image.jpg。"""
    from urllib.parse import unquote, urlparse
    base = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    if base and "." in base.rsplit("/", 1)[-1]:
        return base[:100]
    return "image.jpg"


def _image_mime(fname):
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    return {"png": "image/png", "webp": "image/webp", "gif": "image/gif",
            "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/jpeg")


def upload_image_url(url, timeout_dl=20, timeout_up=60):
    """下载外部商品图 → 上传飞书拿 file_token(附件字段用)。

    parent_type=bitable_image + parent_node=app_token 是 bitable 附件的固定参数
    (实测缺了拿不到合法 token)。进程内按 URL 缓存 file_token;任何一步失败
    返回 None 并记日志,不抛异常 —— 调用方据此留空该图片列,不中断整行回填。
    """
    url = str(url or "").strip()
    if not url:
        return None
    with _IMAGE_LOCK:
        if url in _IMAGE_TOKEN_CACHE:
            return _IMAGE_TOKEN_CACHE[url]
    try:
        r = requests.get(url, timeout=timeout_dl, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120 Safari/537.36")})
    except Exception as exc:
        print(f"[bitable] 下载图片异常 {url[:80]}: {exc}", flush=True)
        return None
    if r.status_code != 200 or not r.content:
        print(f"[bitable] 下载图片失败 HTTP {r.status_code}: {url[:80]}", flush=True)
        return None
    content = r.content
    fname = _image_filename(url)
    token, err = app_client.get_tenant_access_token()
    if err:
        print(f"[bitable] 上传图片取 token 失败: {err}", flush=True)
        return None
    data = {"file_name": fname, "parent_type": "bitable_image",
            "parent_node": _tokens()[0], "size": str(len(content))}
    try:
        ur = requests.post(
            "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
            headers={"Authorization": f"Bearer {token}"},
            data=data,
            files={"file": (fname, content, _image_mime(fname))},
            timeout=timeout_up)
        ud = ur.json()
    except Exception as exc:
        print(f"[bitable] 上传图片异常 {url[:80]}: {exc}", flush=True)
        return None
    if ud.get("code") != 0:
        print(f"[bitable] 上传图片失败 code={ud.get('code')} msg={ud.get('msg')}: "
              f"{url[:80]}", flush=True)
        return None
    ft = (ud.get("data") or {}).get("file_token")
    if not ft:
        return None
    with _IMAGE_LOCK:
        _IMAGE_TOKEN_CACHE[url] = ft
    return ft


def fill_record_images(record_id, urls, table_id=None, field_names=None):
    """把 urls 前 9 张逐个上传写进附件字段 field_names[i](缺省 图1~图9)。

    单图失败只留空该列,其余照填;整行 batch_update 失败返回 0。返回成功填图数。
    """
    if not record_id or not urls:
        return 0
    if field_names is None:
        field_names = IMAGE_COLUMNS
    fields = {}
    filled = 0
    for i, url in enumerate(urls[:len(field_names)]):
        ft = upload_image_url(url)
        if ft:
            fields[field_names[i]] = [{"file_token": ft}]
            filled += 1
    if fields:
        try:
            batch_update([{"record_id": record_id, "fields": fields}], table_id=table_id)
        except Exception as exc:
            print(f"[bitable] 写图字段失败(record={record_id}): {exc}", flush=True)
            return 0
    return filled


def sku_detail_lines(skus):
    """把 product_skus 转「颜色 = 供应商SKU号」多行文本,发货按 SKU 号对应到 SPU。

    兼容 ORM 对象与 dict;无 SKU 号的行只列颜色。"""
    lines = []
    for s in skus or []:
        if isinstance(s, dict):
            color = str(s.get("color") or "").strip()
            sid = str(s.get("supplier_sku_id") or "").strip()
        else:
            color = str(getattr(s, "color", "") or "").strip()
            sid = str(getattr(s, "supplier_sku_id", "") or "").strip()
        if color and sid:
            lines.append(f"{color} = {sid}")
        elif color:
            lines.append(color)
        elif sid:
            lines.append(sid)
    return "\n".join(lines)


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
        "锁定时间", "上架时间", "上架链接", "失败原因", "备注", BATCH_FIELD,
    ] + IMAGE_COLUMNS + [SKU_DETAIL_FIELD])
    _check_single_select(out, listing_tid, "状态", LISTING_STATUSES)
    _check_single_select(out, listing_tid, "店铺站点", SITE_OPTIONS)

    # 选品表(表A)
    out.append((True, "-- 选品采集表(表A) --"))
    _check_table_fields(out, pick_tid, [
        "SPU", "商品ID", "标题(中文)", "分类", "成本价(CNY)", "主图",
        "目标市场", "采集时间", "备注",
    ] + [PICK_MAIN_IMAGE_FIELD, SKU_DETAIL_FIELD])
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
