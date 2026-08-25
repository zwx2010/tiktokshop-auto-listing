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
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import selectinload

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
# 批次级结果含失败件(如 1 成功 1 precheck 失败)、无法逐件定位成功/失败款时,
# 集合内行标「上架待核对」——既不谎报成功(失败的件被盖已上架),也不误伤成功件
# (全标上架失败)。运营据群结果卡/后台核对后人工改状态。
ST_NEEDS_CHECK = "上架待核对"

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


def _row_batch_id(row) -> str:
    f = row.get("fields") or {}
    v = f.get(bitable.BATCH_FIELD) or f.get("任务批次")
    return str(v).strip() if v not in (None, "") else ""


def _robot_upload_authorized(rows) -> bool:
    """只有机器人“上架”指令创建的 sel_* 批次可在审批后上传。"""
    return bool(rows) and all(_row_batch_id(row).startswith("sel_") for row in rows)


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
                p = (db.query(Product).options(selectinload(Product.skus))
                     .filter(Product.spu == spu, Product.active == True).first())
            if p is None and gid:
                p = (db.query(Product).options(selectinload(Product.skus))
                     .filter(Product.source_goods_id == gid,
                             Product.active == True).first())
            if p is None:
                _set_rows([r], ST_FAIL,
                          failure=f"SPU/商品ID 不在采集库(spu={spu or '-'} "
                                  f"gid={gid or '-'}),商品未入库或已被删除")
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
                # 已 ready 也重检:规则库更新后旧文案可能已违规(新禁品牌词/超长),
                # 校验不过 → 走重新生成,不放过带违规文案的 ready 文案
                ok, _reason = _validate_copy(lst.title, lst.description)
                if ok:
                    continue
                print(f"[copy] 商品 {p.source_goods_id} ready 文案未通过重检,重新生成", flush=True)
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


def _ensure_styles_en(products, mkt: str):
    """把组内所有 SKU 的 1688 中文款式翻译成英文,写回 sku.style_en。

    upload 表的「次要销售变体值」不能含中文;文案生成(_ensure_copy)只在缺文案时
    顺带返回 styles_en,已 ready 文案的商品款式没人翻 → 这里兜底:收集组内所有
    未翻译的中文 style,一次 claude 桥批量翻译(真实翻译,不造假),按原样落库。
    已有 style_en 的不重复翻。
    """
    import re as _re
    from ..models import ProductSku

    def _is_cn(s: str) -> bool:
        return bool(_re.search(r"[一-鿿]", s or ""))

    db = SessionLocal()
    try:
        pending: dict[str, str] = {}  # 中文style -> 原文(按原样翻一次,去重)
        for p in products.values():
            for sku in p.skus:
                st = (sku.style or "").strip()
                if st and not (sku.style_en or "").strip() and _is_cn(st):
                    pending.setdefault(st, st)
        if not pending:
            return
        terms = list(pending)
        prompt = (
            "你是 TikTok Shop 配饰类目本地化专员。把下面的中文款式名逐条翻译成"
            "英文买家能看懂的款式词,只译款式本身,不增删不释义、不编材质规格,"
            "不要给整句。逐条一一对应。\n"
            + "\n".join(f"{i}. {t}" for i, t in enumerate(terms)) + "\n"
            "只输出 JSON: {\"items\": [{\"cn\": \"中文原文\", \"en\": \"英文译文\"}]}"
        )
        sr = _run_claude_style(prompt, timeout_s=max(60, 30 * len(terms)))
        items = (sr or {}).get("items") if isinstance(sr, dict) else None
        if not isinstance(items, list):
            print(f"[bitable] style 英译未返回合法 JSON({len(terms)} 条),跳过本组", flush=True)
            return
        en_map = {}
        for it in items:
            if isinstance(it, dict):
                cn = str(it.get("cn") or "").strip()
                en = str(it.get("en") or "").strip()
                if cn and en:
                    en_map[cn] = en
        if not en_map:
            return
        # 游离对象不能直接改(见 _ensure_copy 注释):在本 session 重查再写
        hit = 0
        for sku in (db.query(ProductSku)
                    .filter(ProductSku.style.in_(list(en_map))).all()):
            en = en_map.get((sku.style or "").strip())
            if en and (sku.style_en or "") != en:
                sku.style_en = en
                hit += 1
        if hit:
            db.commit()
            print(f"[bitable] 款式英译完成: {hit}/{len(terms)} 条", flush=True)
    finally:
        db.close()


def _run_claude_style(prompt: str, timeout_s=180):
    """样式翻译专用 claude 桥调用(复用 bridge,单独封装避免循环依赖)。"""
    from scripts.regenerate_listing_copy import _run_claude
    return _run_claude(prompt, timeout_s=timeout_s)


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
        # 子进程 stdout 强制 utf-8(Windows 控制台默认 GBK,不解码 utf-8 中文会变乱码,
        # 下方正则匹配不到「上架表格:」路径 → 误判出表失败)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, "tools/export_platform_to_staging.py",
             "--market", mkt, "--selected-file", sel.name,
             "--run-dir", str(run_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=420, env=env)
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
    # 1.5) 中文款式 → 英文(upload 表变体值不能含汉字;缺 style_en 的兜底翻译)
    _ensure_styles_en(products, mkt)
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
    # 审图生成 *_zhfiltered.xlsx(已剔除无可用图的阻塞行)时,「仅通过审图OK」用它;
    # 判定不看 agent 回传,直接 glob 源表同目录 —— 确定性,不依赖解析。
    filtered_tables = []
    for t in tables:
        cand = os.path.join(os.path.dirname(t),
                            os.path.splitext(os.path.basename(t))[0] + "_zhfiltered.xlsx")
        if os.path.isfile(cand):
            filtered_tables.append(cand)
    tables_ok = filtered_tables or tables
    # 4.5) 审图剔除(图片问题)自动补位:从采集库补失败件数,只补一轮,库空发卡片提示。
    #      被剔行仍由 _write_result 标「上架失败」;补位行是独立新批次,下一轮轮询自动跑。
    _maybe_refill_after_review(mkt, rows, products, tables_ok, gids)
    # 5) 审批卡(按钮回调走现有状态机;通过后 CDP 上架)
    params = {"market": mkt, "mode": "upload", "target": len(gids),
              "upload_authorized": _robot_upload_authorized(rows),
              "tables": tables, "tables_ok": tables_ok,
              "run_dir": os.path.dirname(os.path.dirname(tables[0])) if tables else "",
              "note": f"多维表格选品({len(gids)}件)",
              "source": "bitable"}
    approval.create(run_id, params=params)
    stages = {"table": {"stage_result": {"out_tables": tables}}, "review": review_res}
    card_data = approval.build_card(run_id, stages, candidates=len(gids))
    sent = fc.deliver_card(fc.approval_card(**card_data))
    _set_rows(rows, ST_APPROVE)
    _monitor(run_id, rows)


def _maybe_refill_after_review(mkt, rows, products, tables_ok, gids):
    """审图剔除(图片问题)后自动补位:从采集库补失败件数,只补一轮,库空发卡片。

    被剔行仍由 _write_result 标「上架失败(审图剔除未上传)」,这里只负责:
      - 非补位批次:从库里按「被剔行品类+成本窗口」挑 shortfall 件,生成表B「待上架」
        补位行(batch=refill_*,下一轮轮询自动跑),并如实发结果卡(补足/部分/库空);
      - 补位批次(refill_*)再被剔除:不再补位,发灰卡「已停止补位」,防死循环。
    卡片一律 buttons=[] 只提示不回调。
    """
    passed = _uploaded_gids(tables_ok)
    if passed is None:
        print("[bitable] 补位判定:解析审图后表商品ID失败,跳过补位", flush=True)
        return
    dropped = sorted({str(g) for g in gids} - {str(g) for g in passed})
    shortfall = len(dropped)
    if shortfall <= 0:
        return  # 无剔除,不补位

    titles = [str(getattr(products.get(g), "title_cn", "") or g) for g in dropped]

    def _card(color, result, extra=()):
        fields = [("市场", str(mkt).upper()), ("结果", result),
                  ("被剔商品", "、".join(titles[:8]))] + list(extra)
        fc.deliver_card(fc.approval_card(title="上架机器人", color=color,
                                         fields=fields, buttons=[]))

    if any(_row_batch_id(r).startswith("refill_") for r in rows):
        _card("grey", f"补位批次仍有 {shortfall} 件被审图剔除,已停止补位(补位只做一轮)")
        return

    # 补位选品范围 = 被剔行同品类(select_upload 存的分类已是英文 category)
    cat_by_gid = {_row_goods_id(r): str((r.get("fields") or {}).get("分类") or "").strip()
                  for r in rows}
    en_cat = next((cat_by_gid.get(g, "") for g in dropped if cat_by_gid.get(g, "")), "")
    res = approval.create_refill_rows(mkt, en_cat, shortfall, exclude_gids=set(gids))
    created = res.get("created", 0)
    scope = f"{en_cat or '全品类'}+成本2-40"
    if created >= shortfall:
        _card("blue", f"已从采集库补位 {created} 件(审图剔除{shortfall}件已跳过),新批次自动跑")
    elif created > 0:
        _card("orange", f"仅补位 {created}/{shortfall} 件,库里符合条件的商品不足({scope})")
    else:
        _card("grey", f"库里没有符合条件的补位商品({scope}),未生成补位任务",
              extra=[("提示", "先采集入库,或调整成本/品类条件")])


def _monitor(run_id: str, rows):
    """等审批卡被点(通过→上架跑完;驳回→中止),把最终结果写回表。
    阻塞最长 60 分钟:审批等待不可控(用户可能隔很久才点卡,实测 16 分钟就超时误标失败
    并丢掉点卡后的上传结果),上传本身 15 分钟上限。60 分钟覆盖两者,不误杀审批中的批次。"""
    deadline = time.time() + 3600
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
            # 实际上传的表(transition 已按 decision 选:approve_ok→过滤副本,
            # approve_all→原表)。从 seller_sku 前缀解析商品ID集合,回写时
            # 只把集合内的商品行标「已上架」;被审图剔除(不在集合)的行不掺假。
            up_tables = (st.get("params") or {}).get("tables") or []
            gids = _uploaded_gids(up_tables)
            _write_result(rows, st.get("upload_result") or {}, uploaded_gids=gids)
            # 实际上传完成 → 后台把平台库最新真实文案刷进 RAG 语料
            # (导出写新时间戳目录 → 语料文件集合变化 → 下次检索自动重建索引)
            _refresh_corpus_after_upload()
            return
        time.sleep(5)
    _set_rows(rows, ST_FAIL, failure="等待审批/上架超时(>16分钟)")


def _refresh_corpus_after_upload():
    """上架完成后自动把平台库真实文案刷进 RAG 语料(后台执行)。

    复用 tools/export_corpus_from_platform 的导出:每次写新时间戳目录,
    语料文件集合变化 → 下次检索 ensure_index 自动重建向量索引,无需手动跑。
    后台线程执行,失败只记日志,绝不影响上架结果回写;导出新开子进程,
    与 _export_tables 同款,不依赖 sys.path 里 tools 包可导入。
    """
    def job():
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            proc = subprocess.run(
                [sys.executable, "tools/export_corpus_from_platform.py"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), timeout=300, env=env)
            out = (proc.stdout or "").strip()
            if proc.returncode == 0:
                summary = out.splitlines()[-1] if out else "done"
                print(f"[bitable] 上架完成,自动刷新 RAG 语料: {summary}", flush=True)
            else:
                print(f"[bitable] 自动刷新语料脚本退出码 {proc.returncode}: "
                      f"{((out + (proc.stderr or '')).strip())[-400:]}", flush=True)
        except Exception as exc:
            print(f"[bitable] 上架后自动刷新语料失败(不影响上架结果): {exc}", flush=True)
    threading.Thread(target=job, daemon=True).start()


def _uploaded_gids(tables):
    """从实际上传表提取商品ID集合(seller_sku 列,格式 PDD-PH-<商品ID>-<时间戳>)。

    解析失败返回 None —— 调用方据此退化为整批标注(不误杀整批)。"""
    import openpyxl
    gids = set()
    try:
        for t in (tables or []):
            wb = openpyxl.load_workbook(t, read_only=True)
            ws = wb[wb.sheetnames[0]]
            header = [c.value for c in next(ws.iter_rows(max_row=1))]
            sku_idx = header.index("seller_sku") if "seller_sku" in header else None
            if sku_idx is None:
                wb.close()
                continue
            for row in ws.iter_rows(min_row=6, values_only=True):
                sku = str(row[sku_idx] or "")
                m = re.match(r"[A-Z]+-(?:PH|TH|VN)-(\d{8,})-", sku)
                if m:
                    gids.add(m.group(1))
            wb.close()
    except Exception as exc:
        print(f"[bitable] 解析实际上传商品ID失败: {exc}", flush=True)
        return None
    return gids


def _write_result(rows, result, uploaded_gids=None):
    """上架完成后回写:真实 ok/fail,不粉饰。

    卖家后台只给批次级成功/失败件数,无法逐件定位失败款。三档如实回写:
      - 批次全成功(fail==0):集合内行写「已上架」+ 上架时间;
      - 批次含失败件(fail>0):集合内行写「上架待核对」——既不让失败件被盖
        「已上架」章,也不误伤成功件全标失败;备注如实写批次总数(详情见群结果卡),
        运营核对后台后人工改状态;
      - 不在集合的行 = 被审图剔除未实际上传 → 标「上架失败」+ 如实写原因,
        不写成功时间。
    上架链接只在结果里真有时才写,没有就不写。

    uploaded_gids: 实际上传表里的商品ID集合(审图过滤副本或原表)。非 None 时,
    只把集合内的商品行按上两档标注 —— 杜绝「整批 3 行全标已上架、实际只传 2 件
    且其中 1 件 precheck 失败」的假状态。None(解析失败)时退化为整批按 ok/fail 标注。
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
    remark_batch = f"批次成功{ok}件/失败{fail}件,详情见群结果卡" if fail > 0 else ""
    # 批次级含失败件且无法逐件定位成功/失败款 → 集合内行不能盖「已上架」章。
    # 先确保「上架待核对」选项存在于状态字段,再在下面标给这些行。
    has_partial_failure = fail > 0
    if has_partial_failure:
        bitable.ensure_status_option(ST_NEEDS_CHECK)

    records = []
    for r in rows:
        gid = _row_goods_id(r)
        if uploaded_gids is not None and (not gid or gid not in uploaded_gids):
            # 该商品未进入实际上传表(审图剔除/无可用图),不标已上架,也不写成功时间
            records.append({"record_id": r["record_id"],
                            "fields": {"状态": ST_FAIL,
                                       "失败原因": "审图剔除未上传(无可用图),未实际上架"}})
            continue
        if has_partial_failure:
            # 集合内但批次含失败件:无法确认本件成功还是失败,标待核对(诚实中间态)
            fields = {"状态": ST_NEEDS_CHECK}
            if remark_batch:
                fields["备注"] = remark_batch
            if url:
                fields["上架链接"] = url
            records.append({"record_id": r["record_id"], "fields": fields})
            continue
        fields = {"状态": ST_DONE if ok else ST_FAIL, "上架时间": now_ms}
        if remark_batch:
            fields["备注"] = remark_batch
        if url:
            fields["上架链接"] = url
        records.append({"record_id": r["record_id"], "fields": fields})
    if not records:
        return
    try:
        bitable.batch_update(records)
    except Exception as exc:
        print(f"[bitable] 状态回写失败: {exc}", flush=True)
