"""多维表格驱动的上架流程 —— 上架情况表(表B) → 真实流水线 → 状态/结果回写。

上架表一行 = SPU×店铺站点 的上架任务。运营在上架表把状态标成「待上架」→
轮询捡走(拿行即翻→处理中)→ 按店铺站点分组 → 按 SPU/商品ID 从采集库解析商品 →
补真实文案(缺的用 claude 桥生成)→ 出 TikTok 上架表 → 审图+RAG → 审批卡 →
通过后 CDP 上架。每步把状态写回表,完成后回写 上架时间/上架链接/失败原因。

全程真实,不掺假:
  - 上架行的商品必须已在采集库真实存在(采集链路自动写入选品表并入库);
    查不到的行如实标「上架失败」,绝不从行字段编造补建;
  - 文案生成失败或 RAG 规则拦截 → 该行标「上架失败」,绝不带空/假文案上架;
  - 上架结果是批次级的(卖家后台只给成功/失败件数),无法逐件定位失败款,
    回写时如实写批次总数到「备注」,不粉饰逐件状态。
"""
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from ..config import BASE_DIR
from ..database import SessionLocal
from ..feishu import bitable, client as fc
from . import approval, tasks

# 表A「状态」字段单选取值(与 app/feishu/bitable.py 模块头一致)
ST_TODO = "待上架"
ST_LOCK = "处理中"
ST_COPY = "文案生成中"
ST_QC = "图片质检中"
ST_APPROVE = "审批中"
ST_UPLOADING = "上架中"
ST_DONE = "已上架"
ST_FAIL = "上架失败"
ST_REJECTED = "已驳回"

# 商品ID 规则:与全系统 goods_id 一致(8 位以上纯数字)
_GID_RE = re.compile(r"\d{8,}")


class FlowError(Exception):
    """批次流程错误,调用方 catch 后如实标失败。"""


class CopyError(FlowError):
    """文案生成失败/RAG 拦截。绝不带空/假文案上架。"""


class ExportError(FlowError):
    """出上架表失败。"""


def _workflow_dir() -> Path:
    """与 export_platform_to_staging.py 同源:工作流根目录(runs 落这里)。"""
    env = os.environ.get("TIKTOK_WORKFLOW_DIR")
    if env:
        return Path(env)
    return BASE_DIR.parent / "tk自动化工作流"


def _row_goods_id(row) -> str:
    f = row.get("fields") or {}
    for k in ("商品ID", "商品id", "goods_id"):
        v = f.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _row_spu(row) -> str:
    f = row.get("fields") or {}
    v = f.get("SPU")
    return str(v).strip() if v not in (None, "") else ""


# 上架表「店铺站点」单选 → 市场代码
_SITE_TO_MARKET = {"TH店铺": "th", "PH店铺": "ph", "VN店铺": "vn"}


def _site_to_market(row) -> str:
    """从上架表行解析市场:优先「店铺站点」,兼容直接填的市场代码/旧「目标市场」。"""
    f = row.get("fields") or {}
    site = str(f.get("店铺站点") or "").strip()
    if site in _SITE_TO_MARKET:
        return _SITE_TO_MARKET[site]
    low = site.lower()
    if low in ("th", "ph", "vn"):
        return low
    mkt = str(f.get("目标市场") or "").strip().lower()
    return mkt if mkt in ("th", "ph", "vn") else "th"


def _set_rows(rows, status, remark="", failure=""):
    """批量回写状态;写失败只记日志,不中断流水线(状态看板尽力而为)。"""
    records = []
    for r in rows:
        fields = {"状态": status}
        if remark:
            fields["备注"] = remark
        if failure:
            fields["失败原因"] = failure[:200]
        records.append({"record_id": r["record_id"], "fields": fields})
    if not records:
        return
    try:
        bitable.batch_update(records)
    except Exception as exc:
        print(f"[bitable] 状态回写失败({status}): {exc}", flush=True)


# ---------------------------------------------------------------- 入库/补文案
def _ensure_products(rows):
    """按 SPU(优先)/商品ID 从平台库解析 Product —— 只引用,不补建。

    上架表行必须指向采集库真实存在的商品(采集链路自动写入选品表并入库)。
    查不到的行 → 单独标「上架失败」(SPU/商品ID 不在采集库),并从本批剔除,
    不拖垮同批其他行;绝不从行字段编造补建商品。
    顺带把 SPU 解析到的 标题(中文)/分类/成本 回填到行(空才填),店铺视角好看。
    返回 (found: {goods_id: Product}, valid_rows: 可继续处理的行)。
    """
    from ..models import Product
    db = SessionLocal()
    try:
        found: dict[str, Product] = {}
        valid: list = []
        for r in rows:
            spu = _row_spu(r)
            gid = _row_goods_id(r)
            p = None
            if spu:
                p = db.query(Product).filter(Product.spu == spu).first()
            if p is None and gid:
                p = db.query(Product).filter(
                    Product.source_goods_id == gid).first()
            if p is None:
                _set_rows([r], ST_FAIL,
                          failure=f"SPU/商品ID 不在采集库(spu={spu or '-'} "
                                  f"gid={gid or '-'}),请先采集入库")
                print(f"[bitable] 行不在采集库,标失败: {spu or gid}", flush=True)
                continue
            found.setdefault(p.source_goods_id or f"B{p.id}", p)
            # 回填展示字段(空才填,不覆盖运营手动内容)
            f = r.get("fields") or {}
            patch = {}
            if not f.get("标题(中文)"):
                patch["标题(中文)"] = (p.title_cn or "")[:200]
            if not f.get("分类"):
                patch["分类"] = p.category or ""
            if f.get("成本价(CNY)") in (None, ""):
                patch["成本价(CNY)"] = float(p.cost_cny_used or 0)
            if patch:
                try:
                    bitable.update_record(r["record_id"], patch)
                except Exception as exc:
                    print(f"[bitable] 回填行字段失败: {exc}", flush=True)
            valid.append(r)
        return found, valid
    finally:
        db.close()


def _seller_sku(p, mkt: str) -> str:
    gid = p.source_goods_id or f"B{p.id}"
    return re.sub(r"[^A-Z0-9-]", "", f"BITABLE-{mkt}-{gid}".upper())[:60]


def _ensure_copy(products, mkt: str):
    """确保每件商品有真实 Listing 文案。

    已有 ready 文案 → 复用;没有 → 用 claude 桥真实生成(注入 RAG 规则 + 真实范例,
    与 scripts/regenerate_listing_copy.py 同源)。生成失败/RAG 拦截 → CopyError,
    调用方标「上架失败」,绝不带空/假文案出表。
    """
    from scripts.regenerate_listing_copy import (
        _rag_rules_summary, _llm_batch_prompt, _run_claude, _validate_copy,
        _rag_examples, _sku_attrs, DEFAULT_STORE,
    )
    from ..models import Account, Listing
    db = SessionLocal()
    try:
        account = db.query(Account).first()
        acc_id = account.id if account else None
        need: list[tuple] = []
        for p in products.values():  # products 是 {goods_id: Product}
            lst = (db.query(Listing)
                   .filter(Listing.product_id == p.id,
                           Listing.market_code == mkt.upper()).first())
            if lst and lst.listing_status == "ready" and lst.title and lst.description:
                continue
            if lst is None:
                lst = Listing(product_id=p.id, account_id=acc_id,
                              market_code=mkt.upper(),
                              seller_sku=_seller_sku(p, mkt),
                              listing_status="copy_missing")
                db.add(lst)
                db.flush()
            need.append((p, lst))
        db.commit()
        if not need:
            return
        entries = []
        for p, lst in need:
            colors, styles = _sku_attrs(p)
            entries.append({
                "goods_id": p.source_goods_id or f"B{p.id}",
                "style_code": (p.source_goods_id or f"B{p.id}")[-4:],
                "title_cn": p.title_cn,
                "category": p.category,
                "colors": colors,
                "styles": styles,
                "markets": [mkt.upper()],
                "examples": {mkt.upper(): _rag_examples(mkt.lower(), p)},
                "listing_refs": [lst],
            })
        prompt = _llm_batch_prompt(entries, DEFAULT_STORE, _rag_rules_summary())
        sr = _run_claude(prompt, timeout_s=max(240, 180 * len(entries)))
        items = (sr or {}).get("items") if isinstance(sr, dict) else None
        item_map = {}
        for it in (items or []):
            if isinstance(it, dict) and it.get("goods_id"):
                item_map[str(it["goods_id"])] = it
        for p, lst in need:
            row = (item_map.get(p.source_goods_id) or {}).get(mkt.upper()) or {}
            title = (row.get("title") or "").strip()
            desc = (row.get("description") or "").strip()
            ok, reason = _validate_copy(title, desc)
            if title and desc and ok:
                lst.title = title
                lst.description = desc
                lst.listing_status = "ready"
                db.commit()
            else:
                why = f"RAG拦截:{reason}" if not ok else "未返回合法文案"
                raise CopyError(f"{p.source_goods_id} 文案生成失败({why})")
    finally:
        db.close()


# ---------------------------------------------------------------- 出表/审图
def _export_tables(mkt: str, gids):
    """表里选中的商品ID → 临时选品表 → export 人工选品路径 → TikTok 上架表 xlsx。

    复用现有人工选品(--selected-file)逻辑,不新增 export 参数;
    返回 [xlsx 绝对路径]。失败抛 ExportError(带真实 stdout 摘要)。
    """
    import tempfile
    import openpyxl
    bad = [g for g in gids if not _GID_RE.fullmatch(str(g))]
    if bad:
        raise ExportError(f"商品ID 需 8 位以上纯数字,格式不符: {bad[:5]}")
    sel = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    sel.close()
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "选品"
        ws.append(["商品ID"])
        for g in gids:
            ws.append([str(g)])
        wb.save(sel.name)
        wb.close()  # 必须先关 workbook,Windows 上文件句柄才释放,否则下方 unlink 失败
        run_dir = _workflow_dir() / "runs" / f"bitable_export_{time.strftime('%Y%m%d_%H%M%S')}" / mkt.lower()
        proc = subprocess.run(
            [sys.executable, "tools/export_platform_to_staging.py",
             "--market", mkt, "--selected-file", sel.name,
             "--run-dir", str(run_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=420)
    finally:
        try:
            os.unlink(sel.name)
        except OSError:
            pass
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"上架表格:\s*(\S+\.xlsx)", out)
    if proc.returncode != 0 or not m:
        raise ExportError(f"出表失败(exit={proc.returncode}): {out[-600:]}")
    return [m.group(1)]


def _run_review(mkt: str, tables):
    """审图 + RAG 规则(真实脚本,复用现有 review stage)。"""
    p = {"market": mkt, "tables": tables,
         "run_dir": os.path.dirname(os.path.dirname(tables[0])) if tables else "",
         "sample_title": approval._db_sample_title(mkt) or "项链",
         "cost": approval._db_sample_cost(mkt) or 8}
    return tasks.run_stage("review", p, timeout_s=420)


# ---------------------------------------------------------------- 主流程
def run_picked(rows):
    """轮询捡到的上架表行(已翻成处理中)。按店铺站点分组,每组跑一次真实流水线。
    任一组失败只标失败,不影响其他组。"""
    groups: dict[str, list] = {}
    for r in rows:
        mkt = _site_to_market(r)
        groups.setdefault(mkt, []).append(r)
    for mkt, group in groups.items():
        try:
            _process_group(mkt, group)
        except FlowError as exc:
            _set_rows(group, ST_FAIL, failure=str(exc))
            print(f"[bitable] 批次失败({mkt}): {exc}", flush=True)
        except Exception as exc:
            _set_rows(group, ST_FAIL, failure=f"{type(exc).__name__}: {exc}")
            print(f"[bitable] 批次异常({mkt}): {exc}", flush=True)


def _process_group(mkt: str, rows):
    run_id = uuid.uuid4().hex[:8]

    # 1) 解析商品(必须已在采集库;查不到的行已各自标失败并剔除)
    products, rows = _ensure_products(rows)
    if not rows:
        return  # 全部不在采集库,已标失败,不再跑流水线
    gids = [g for g in (_row_goods_id(r) for r in rows) if g]
    if not gids:
        raise FlowError("选中的行没有「商品ID」字段,无法出表")

    # 2) 补真实文案(缺的 claude 桥生成;失败 → 标失败,不造假)
    _set_rows(rows, ST_COPY)
    _ensure_copy(products, mkt)
    # 3) 出上架表
    tables = _export_tables(mkt, gids)
    # 4) 审图 + RAG 规则
    _set_rows(rows, ST_QC)
    review_res = _run_review(mkt, tables)
    # 5) 审批卡(按钮回调走现有状态机;通过后 CDP 上架)
    params = {"market": mkt, "mode": "upload", "target": len(gids),
              "tables": tables,
              "run_dir": os.path.dirname(os.path.dirname(tables[0])) if tables else "",
              "note": f"多维表格选品({len(gids)}件)",
              "source": "bitable"}
    approval.create(run_id, params=params)
    stages = {"table": {"stage_result": {"out_tables": tables}}, "review": review_res}
    card_data = approval.build_card(run_id, stages, candidates=len(gids))
    sent = fc.deliver_card(fc.approval_card(**card_data))
    _set_rows(rows, ST_APPROVE)
    _monitor(run_id, rows)


def _monitor(run_id: str, rows):
    """等审批卡被点(通过→上架跑完;驳回→中止),把最终结果写回表。
    阻塞最多 ~16 分钟(审批等待 + 上传 15 分钟上限)。"""
    deadline = time.time() + 1000
    while time.time() < deadline:
        st = approval.get(run_id) or {}
        status = st.get("status")
        if status == "rejected":
            _set_rows(rows, ST_REJECTED, remark="运营驳回,未上架")
            return
        if st.get("upload_status") == "skipped":
            _set_rows(rows, ST_REJECTED, remark="review_only 模式,未上架")
            return
        if st.get("upload_status") == "done":
            _write_result(rows, st.get("upload_result") or {})
            return
        time.sleep(5)
    _set_rows(rows, ST_FAIL, failure="等待审批/上架超时(>16分钟)")


def _write_result(rows, result):
    """上架完成后回写:真实 ok/fail,不粉饰。

    卖家后台只给批次级成功/失败件数,无法逐件定位失败款 → 成功行写「已上架」
    并写 上架时间(完成时刻),有失败时在「备注」如实写批次总数(详情见群结果卡),
    不编造逐件状态。上架链接只在结果里真有时才写,没有就不写。
    """
    result = result or {}
    if result.get("ok") is False:
        _set_rows(rows, ST_FAIL, failure=str(result.get("error") or "上架失败")[:200])
        return
    sr = result.get("stage_result")
    sr = sr if isinstance(sr, dict) else {}
    ok = int(sr.get("ok") or 0)
    fail = int(sr.get("fail") or 0)
    now_ms = int(time.time() * 1000)
    url = str(sr.get("url") or "").strip()
    if fail > 0:
        records = [{"record_id": r["record_id"],
                    "fields": {"状态": ST_DONE if ok else ST_FAIL,
                               "上架时间": now_ms,
                               "备注": f"批次成功{ok}件/失败{fail}件,详情见群结果卡"}}
                   for r in rows]
        if url:
            for rec in records:
                rec["fields"]["上架链接"] = url
        try:
            bitable.batch_update(records)
        except Exception as exc:
            print(f"[bitable] 状态回写失败({ST_DONE if ok else ST_FAIL}): {exc}",
                  flush=True)
        return
    records = [{"record_id": r["record_id"],
                "fields": {"状态": ST_DONE, "上架时间": now_ms}}
               for r in rows]
    if url:
        for rec in records:
            rec["fields"]["上架链接"] = url
    try:
        bitable.batch_update(records)
    except Exception as exc:
        print(f"[bitable] 状态回写失败({ST_DONE}): {exc}", flush=True)
