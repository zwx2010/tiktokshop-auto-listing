"""审批状态机(内存) + 飞书回调接线 —— 采集→待审→通过/驳回→上架。

状态存内存 dict(key=run_id),运行期够用;M4 可落库到 platform.db 的 Task.state 做持久化。
"""
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

from ..feishu import client as fc
from . import tasks

_lock = threading.Lock()
_STATE = {}          # run_id -> {status, params, decision, created_at, updated_at}
_TRIGGER_UPLOAD = True   # 审批通过后自动触发真实 CDP 上架;测试可关
_REPOSITORY_FACTORY = None


def configure_repository_factory(factory):
    """注入持久化审批仓储工厂；传 None 恢复内存测试模式。"""
    global _REPOSITORY_FACTORY
    _REPOSITORY_FACTORY = factory


def _repository():
    if _REPOSITORY_FACTORY is not None:
        return _REPOSITORY_FACTORY()
    try:
        from ..database import SessionLocal
        if SessionLocal is None:
            return None
        from ..infrastructure.approval_repository import ApprovalRunRepository
        return ApprovalRunRepository(SessionLocal())
    except Exception:
        return None


def _close_repository(repo):
    if repo is not None:
        repo.session.close()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def create(run_id, params=None, card_data=None):
    st = {"status": "pending", "params": params or {}, "card_data": card_data or {},
          "decision": "", "created_at": _now(), "updated_at": _now(),
          "last_error": "", "upload_result": None}
    repo = _repository()
    if repo is not None:
        try:
            row = repo.create(run_id, params=params, card_data=card_data)
            st = repo.as_dict(row)
            st.setdefault("last_error", "")
            st.setdefault("upload_result", None)
            with _lock:
                _STATE[run_id] = st
        finally:
            _close_repository(repo)
    else:
        with _lock:
            _STATE[run_id] = st
    return run_id


def get(run_id):
    repo = _repository()
    if repo is not None:
        try:
            row = repo.get(run_id)
            if row is not None:
                st = repo.as_dict(row)
                with _lock:
                    _STATE[run_id] = dict(st)
                return st
        finally:
            _close_repository(repo)
    with _lock:
        st = _STATE.get(run_id)
        return dict(st) if st else None


def list_runs():
    repo = _repository()
    if repo is not None:
        try:
            rows = repo.list_all()
            return [{"run_id": r.run_id, **repo.as_dict(r)} for r in rows]
        finally:
            _close_repository(repo)
    with _lock:
        return [{"run_id": rid, **dict(v)} for rid, v in _STATE.items()]


def transition(run_id, decision, trigger_upload=None):
    """decision: approve_all / approve_ok / reject。通过则后台真上架,立即返回。"""
    trigger = _TRIGGER_UPLOAD if trigger_upload is None else trigger_upload
    repo = _repository()
    if repo is not None:
        try:
            changed = repo.transition(run_id, decision)
            if changed is None:
                row = repo.get(run_id)
                if row is None:
                    return {"ok": False, "detail": f"unknown run {run_id}"}
                return {"ok": False, "detail": f"already {row.status}"}
            snapshot = dict(changed)
            with _lock:
                _STATE[run_id] = dict(snapshot)
        finally:
            _close_repository(repo)
    else:
        with _lock:
            st = _STATE.get(run_id)
            if not st:
                return {"ok": False, "detail": f"unknown run {run_id}"}
            if st["status"] != "pending":
                return {"ok": False, "detail": f"already {st['status']}"}
            st["status"] = "approved" if decision != "reject" else "rejected"
            st["decision"] = decision
            st["updated_at"] = _now()
            snapshot = dict(st)
    mode = (snapshot.get("params") or {}).get("mode", "upload")
    if snapshot["status"] == "approved" and trigger:
        # 只审不传:审批通过也不触发上架(用户只要求审图把关)
        if mode == "review_only":
            with _lock:
                st = _STATE.get(run_id)
                if st:
                    st["upload_status"] = "skipped"
            repo = _repository()
            if repo is not None:
                try:
                    repo.set_upload_state(run_id, "skipped")
                finally:
                    _close_repository(repo)
            return {"ok": True, "run_id": run_id, "status": snapshot["status"],
                    "decision": decision, "upload_status": "skipped",
                    "detail": "review_only:审批通过,不触发上架(只审不传)"}
        # 「仅通过审图OK」:用审图过滤副本(已剔除无可用图的阻塞行)上传;
        # 「通过全部」:全量原表。params 无 tables_ok(未生成过滤副本)时退化为原表。
        params = dict(snapshot.get("params") or {})
        if decision == "approve_ok":
            ok_tables = params.get("tables_ok")
            if ok_tables:
                params["tables"] = ok_tables
        # 把最终实际使用的表(approve_ok 时已换成审图过滤副本)写回状态。
        # _monitor 回写结果时按它解析商品ID集合 —— 被审图剔除、不在集合内的
        # 行不会被误标「已上架」。此前只改了本地副本传给上传,状态里的 tables
        # 仍是原始全量表,导致「整批 3 行全标已上架、实际只传 2 件」的假状态。
        with _lock:
            st = _STATE.get(run_id)
            if st:
                st["params"] = params
        repo = _repository()
        if repo is not None:
            try:
                repo.update_params(run_id, params)
            finally:
                _close_repository(repo)
        # 真上架耗时数分钟,不在回调里同步等(飞书 3s 超时)。
        # 后台线程跑真实上传,完成后推「上架结果卡」回群。
        _launch_upload(run_id, params)
    upload_status = ""
    with _lock:
        st = _STATE.get(run_id)
        if st:
            upload_status = st.get("upload_status", "")
    return {"ok": True, "run_id": run_id, "status": snapshot["status"],
            "decision": decision, "upload_status": upload_status}


def _launch_upload(run_id, params):
    """后台线程跑 upload stage(真实 CDP 上架),结果写回状态并推结果卡。"""
    with _lock:
        st = _STATE.get(run_id)
        if st:
            st["upload_status"] = "running"
            st["upload_result"] = None
    repo = _repository()
    if repo is not None:
        try:
            repo.set_upload_state(run_id, "running", result=None)
        finally:
            _close_repository(repo)

    def _run():
        try:
            # 真实 CDP 上传 10 款要几分钟,默认 300s 不够;给足 15 分钟
            result = tasks.run_stage("upload", {**params, "run_id": run_id}, timeout_s=900)
        except Exception as exc:
            result = {"ok": False, "stage": "upload", "error": str(exc)}
        with _lock:
            st = _STATE.get(run_id)
            if st:
                st["upload_status"] = "done"
                st["upload_result"] = result
        repo = _repository()
        if repo is not None:
            try:
                repo.set_upload_state(run_id, "done", result=result,
                                      error="" if result.get("ok") else str(result.get("error") or ""))
            finally:
                _close_repository(repo)
        _push_result_card(run_id, result)

    threading.Thread(target=_run, name=f"upload-{run_id}", daemon=True).start()


def _push_result_card(run_id, result):
    """上架完成后推一张结果卡回群(真实 ok/fail,不做粉饰)。"""
    if not result or not result.get("ok"):
        detail = str((result or {}).get("error") or (result or {}).get("detail") or "上架失败")
        fc.deliver_card(fc.approval_card(
            title=f"上架结果 #{run_id}", color="red",
            fields=[("状态", "上架失败"), ("原因", detail[:100]), ("run_id", run_id)],
            buttons=[]))
        return
    sr = result.get("stage_result") or {}
    # 兼容 stage_result 为字符串的情况(claude bridge 有的 stage 返回纯文本而非
    # 结构化 dict,此前直接 sr.get 抛 AttributeError 导致结果卡没推出来)。
    if isinstance(sr, dict):
        ok, fail = sr.get("ok") or 0, sr.get("fail") or 0
        detail = str(sr.get("mode") or "?")
    else:
        ok, fail = 0, 0
        detail = str(sr)[:120] if sr else "(上架完成,结果未结构化)"
    color = "green" if ok and fail == 0 else "red"
    fc.deliver_card(fc.approval_card(
        title=f"上架结果 #{run_id}", color=color,
        fields=[("模式", detail), ("成功", str(ok)),
                ("失败", str(fail)), ("run_id", run_id)],
        buttons=[]))


# ---------------------------------------------------------------- 飞书回调
def on_card_action(body):
    """卡片按钮回调。兼容两种 body 结构:
    - 事件订阅卡片回调: {action:{value:{run_id, action}}}
    - 卡片回调 URL(schema 2.0): {event:{action:{value:{run_id, action}}}}
    两种都把 run_id / action 放在 action.value 里,这里统一取。"""
    event = body.get("event") or {}
    act = event.get("action") or body.get("action") or {}
    value = act.get("value") or {}
    run_id = value.get("run_id") or body.get("run_id")
    decision = value.get("action") or body.get("action")
    if not run_id or not decision:
        return {"ok": False, "detail": "missing run_id/action"}
    return transition(run_id, decision)


def on_message(body):
    """事件订阅消息回调(降级路径:自建应用订阅消息 → 群关键词审批)。"""
    try:
        event = body.get("event") or {}
        # schema 2.0: 事件类型在 header.event_type;schema 1.0: 在 event.type
        header = body.get("header") or {}
        etype = (event.get("type") or header.get("event_type")
                 or body.get("type"))
        if etype != "im.message.receive_v1":
            print(f"[on_message] ignored type={etype}", flush=True)
            return {"ok": True, "ignored": etype}
        msg = event.get("message") or {}
        content = json.loads(msg.get("content") or "{}").get("text", "")
        print(f"[on_message] receive_v1 content={content!r}", flush=True)
    except Exception:
        content = ""
    return handle_instruction(content)


# ---------------------------------------------------------------- 编排
_MARKET_ALIASES = [
    # (关键词, market) 按优先级排:先长词后短词,避免 "泰" 先命中 "泰国店"
    ("泰国", "th"), ("泰", "th"), ("th", "th"), ("泰文", "th"),
    ("越南", "vn"), ("越", "vn"), ("vn", "vn"), ("越文", "vn"),
    ("菲律宾", "ph"), ("菲", "ph"), ("ph", "ph"), ("英文", "ph"),
]
_MARKET_LABEL = {"ph": "菲律宾 PH", "th": "泰国 TH", "vn": "越南 VN"}
_MODE_LABEL = {"upload": "上架", "review_only": "只审不传", "capture_only": "只采集",
               "capture_db": "采集入库", "select_upload": "从采集库生成上架任务",
               "stop": "不执行", "reject": "无法理解"}


def _detect_market(text):
    """从指令文本识别市场;识别不到返回 "ph"(现有流水线默认)。"""
    low = (text or "").lower()
    for kw, mkt in _MARKET_ALIASES:
        if kw in low:
            return mkt
    return "ph"


# 消息里有没有"想让我们干活"的信号;没有(纯问候/闲聊/无关问题)判 reject,不触发流水线
_ACTION_HINTS = ("采", "审", "传", "跑", "出", "表", "批", "货", "款", "看",
                 "生成", "做", "搞", "弄", "来", "上")


def _is_actionable(t):
    """是否像一条要干活的指令(正则兜底用)。带数字 / 命中动作词 → True。"""
    t = (t or "").strip()
    if not t:
        return False
    if re.search(r"\d", t):
        return True
    return any(h in t for h in _ACTION_HINTS)


def _detect_mode(text):
    """从指令文本识别模式(正则兜底,优先长词)。"""
    t = (text or "").strip()
    # 明确说不执行 → stop(绝不跑全流水线)
    if re.search(r"(暂停|先别|别跑|别动|不要跑|不用跑|取消|不执行|停下|先不要|先别跑)", t):
        return "stop"
    # 采集并写进平台库(优先级高于 capture_only:到库信号更具体)
    if re.search(r"(到库|入库|存库|进库|存进库里|收进库里|写进库)", t):
        return "capture_db"
    # 只采集,不制表不审图不上架
    if re.search(r"(只采|先采|采集一下|就采)", t):
        return "capture_only"
    # 只审不传:跑完采集/制表/审图,审批通过也不上架
    if re.search(r"(只审|先审|不传|别传|只看不|先看看|不上架|别上架)", t):
        return "review_only"
    # 从采集库选商品 → 生成上架表(表B)行,由轮询跑真实流水线。
    # 命中形态五类:
    #   1) "从采集库/从库/从选品/选品库 ... 选/上 ..."
    #   2) "选 N件/个 <品类> (上架)到 <站点>"(如 选3件耳环到TH)
    #   3) "上架/选 N个/件 <品类> 到 <站点>"(含中文数字,如 上架三个耳环到ph站点)
    #   4) "上架/选 <站点> N个/件 <品类>"(站点词在数量前,如 上架ph站点3件耳环)
    #   5) "上架/选 N个/件 + <站点>" 无品类(用户定规:提到上架没提采集,就不重新采集,
    #      从采集库选 N 件,品类不限;如 上架ph站点3件 / 上架10件到TH)
    #   6) "上架/选 N个/件" 无站点无品类(如 上架3件 / 上架3件耳环)——同样从库选,站点默认 ph;
    #      模糊量词(一批/一些/几个)也算有数量信号(如 上架一批商品 → 从库选,数量默认 10)
    site_pat = r"(泰国|泰|越南|越|菲律宾|菲|th|ph|vn|泰文|越文|英文)"
    count_sig = r"([0-9一二两三四五六七八九十]+)?(件|个)|(一批|一些|一拨|几个|好几|多件|多款)"
    if re.search(
        (r"(从采集库|从库|从选品|选品库).*(选|上)"
         + r"|选.*(件|个).*(上架|到" + site_pat + ")"
         + r"|(上架|选).*(件|个)(项链|耳环|手链|戒指|发饰|配饰).*(到)?(了)?" + site_pat
         + r"|(上架|选).*" + site_pat + r".*(件|个)(项链|耳环|手链|戒指|发饰|配饰)"
         + r"|(上架|选).*(件|个).*(到)?(了)?" + site_pat
         + r"|(上架|选).*" + site_pat + r".*(件|个)"
         + r"|(上架|选)[^\n]*(" + count_sig + r")(?!.*(采集|入库|到库|进库|存库))"),
        t, re.I):
        return "select_upload"
    # 明确"采集N个品" → 采集入库到选品库(采集→入库→同步选品表,不做制表/审图/上架)。
    # 放 select_upload 之后:"从采集库选3件耳环到TH" 已先命中 select_upload,不会被误判成纯采集。
    if "采集" in t and "采集库" not in t:
        return "capture_db"
    # 无关/闲聊/问候,没有任何干活信号 → 无法理解,不执行
    if not _is_actionable(t):
        return "reject"
    return "upload"


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(s):
    """中文数字 → int。支持 一~九/十/十几/几十/几十几;非数字返回 None。"""
    if not s:
        return None
    if s == "十":
        return 10
    if "十" in s:
        parts = s.split("十")
        tens = _CN_DIGITS.get(parts[0]) if parts[0] else 1
        ones = _CN_DIGITS.get(parts[1]) if len(parts) > 1 and parts[1] else 0
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(s) == 1:
        return _CN_DIGITS.get(s)
    return None


def parse_instruction(text):
    """从群指令里解析采集参数(正则兜底;主路径是 _interpret 的 LLM agent)。"""
    text = text or ""
    # 先剥掉 @机器人/@_user_1 等提及token,否则 "10个" 前先匹配到 mention 里的数字
    text = re.sub(r"@\S+", "", text)
    m = re.search(r"(\d+)", text)
    if m:
        target = int(m.group(1))
    else:
        # 中文数字兜底:"三个"→3,"十五件"→15。必须带量词 件/个 才认,
        # 避免"星期三"里的"三"误判。
        cm = re.search(r"([一二两三四五六七八九十]+)[件个]", text)
        target = _cn_to_int(cm.group(1)) if cm else 0
        if not target:
            # 模糊量词兜底:"一批/一些/一拨/几个" → 默认 10(用户定规)。
            # 只在没有显式数字/中文数字时才认,不抢明确数量。
            target = 10 if re.search(r"(一批|一些|一拨|几个|好几|多件|多款)", text) else 0
    cat = next((kw for kw in ("项链", "耳环", "手链", "戒指", "发饰", "配饰")
                if kw in text), "")
    return {"target": target, "category": cat,
            "market": _detect_market(text), "mode": _detect_mode(text),
            "source": "feishu", "note": ""}


def _interpret(text):
    """主路径:把剥掉 @ 后的原始消息交给 interpret agent(LLM)理解意图。

    用 skill + RAG 事实(市场/品类/成本窗口)解析数量/市场/模式/品类;
    agent 结果非法或调用失败时,回退到正则 parse_instruction(永远有兜底)。
    返回规范化意图 dict:
      {target, market, category, mode, note, source}
    """
    clean = re.sub(r"@\S+", "", text or "").strip()
    fallback = parse_instruction(clean)  # 正则兜底
    try:
        # LLM 理解是加分项;超时/失败立即走正则兜底,不等满 120s
        res = tasks.run_stage("interpret", {"message": clean}, timeout_s=45)
        sr = _stage_dict(res)
        note = str(sr.get("note") or "").strip()
    except Exception as exc:
        print(f"[interpret] stage error: {exc}", flush=True)
        sr, note = {}, ""

    def _int(v):
        try:
            n = int(v)
            return n if n >= 0 else 0
        except (TypeError, ValueError):
            return None

    # 逐字段校验:LLM 给的值可信才用,否则用正则兜底(不信任未校验的 LLM 输出)
    target = _int(sr.get("target"))
    if target is None:
        target = fallback["target"]
    market = str(sr.get("market") or "").strip().lower()
    if market not in ("ph", "th", "vn"):
        market = fallback["market"]
    mode = str(sr.get("mode") or "").strip()
    if mode not in ("upload", "review_only", "capture_db", "capture_only",
                    "select_upload", "stop", "reject"):
        mode = fallback["mode"]
    # select_upload 是强信号(正则命中:数量+品类+目标站点):LLM 提示词虽已补该模式,
    # 仍可能误报成 upload,这里以正则为准 —— 防止"选品上架"指令被误跑成全流水线。
    if fallback["mode"] == "select_upload":
        mode = "select_upload"
    # capture_db 是强信号(正则命中:含"采集"且非"采集库"):LLM 提示词虽已补该模式,
    # 仍可能误报成 upload,这里以正则为准 —— 防止"采集50个品"被误跑成全流水线上架。
    if fallback["mode"] == "capture_db" and "采集" in clean and "采集库" not in clean:
        mode = "capture_db"
    category = str(sr.get("category") or "").strip()
    if category not in ("项链", "耳环", "手链", "戒指", "发饰", "配饰"):
        category = fallback["category"]
    return {"target": target, "market": market, "category": category,
            "mode": mode, "note": note or f"LLM解析({sr.get('market')}/{sr.get('mode')}/{sr.get('target')})"
            if sr else f"正则兜底:{clean[:30]}", "source": "interpret" if sr else "regex"}


def _db_sample_cost(market=""):
    """从平台库取一个真实成本价(cost_cny_used)做 RAG 抽查。

    上架表 price 列是本地化售价(如 254),不是成本;RAG 的 --cost 要
    [2,40] 窗口内的人民币成本。export 脚本本身只选窗口内商品,库里的
    成本都是真值,取一个即可。读不到返回 None。
    """
    from ..config import BASE_DIR
    db = os.path.join(str(BASE_DIR), "data", "platform.db")
    if not os.path.exists(db):
        return None
    try:
        import sqlite3
        con = sqlite3.connect(db)
        cur = con.cursor()
        q = ("SELECT cost_cny_used FROM products "
             "WHERE cost_cny_used IS NOT NULL AND cost_cny_used BETWEEN 2 AND 40")
        args = []
        if market:
            q += " AND UPPER(market_code) = ?"
            args.append(market.upper())
        q += " ORDER BY RANDOM() LIMIT 1"
        cur.execute(q, args)
        row = cur.fetchone()
        con.close()
        return float(row[0]) if row and row[0] else None
    except Exception:
        return None


def _db_sample_title(market=""):
    """从平台库取一个真实中文标题做 RAG 抽查样例。

    RAG 规则层的 required_title_patterns 全是中文词(发圈/耳环/项链…),
    上架表里的 product_name 是英文,喂给它恒判 category 失败(误报)。
    这里跟 _db_sample_cost 同窗口抽样一条 title_cn,代表当前候选集。
    读不到返回 ""。
    """
    from ..config import BASE_DIR
    db = os.path.join(str(BASE_DIR), "data", "platform.db")
    if not os.path.exists(db):
        return ""
    try:
        import sqlite3
        con = sqlite3.connect(db)
        cur = con.cursor()
        q = ("SELECT title_cn FROM products WHERE title_cn IS NOT NULL "
             "AND trim(title_cn) != '' AND cost_cny_used BETWEEN 2 AND 40")
        args = []
        if market:
            q += " AND UPPER(market_code) = ?"
            args.append(market.upper())
        q += " ORDER BY RANDOM() LIMIT 1"
        cur.execute(q, args)
        row = cur.fetchone()
        con.close()
        return str(row[0]).strip() if row and row[0] else ""
    except Exception:
        return ""


def _first_row(table):
    """读上架表首个真商品行,取 标题(product_name)/价格(price) 做 RAG 抽查样例。

    上架表 xlsx 顶部有模板说明行(create_product/metric 等),需扫描到含
    product_name 的英文表头,再跳过模板标记行取首个真商品。真表真数据,
    读不到就返回空(编排用默认值兜底,不报错)。
    """
    import os
    if not table or not os.path.exists(table):
        return {}, ""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(table, read_only=True)
        ws = wb["Template"] if "Template" in wb.sheetnames else wb.active
        rows = ws.iter_rows(values_only=True)
        header = None
        for r in rows:
            if any(isinstance(c, str) and c.strip() == "product_name" for c in (r or [])):
                header = [str(c) if c is not None else "" for c in r]
                break
        if not header:
            wb.close()
            return {}, table
        pi = next((i for i, h in enumerate(header) if h.strip() == "product_name"), None)
        pr = next((i for i, h in enumerate(header) if h.strip() == "price"), None)
        skip = {"create_product", "metric", "category_v2", "商品名称", "产品名称",
                "必填", "选填", "商品描述", "产品描述"}
        for r in rows:
            if not r or not any(c is not None and str(c).strip() for c in r):
                continue
            title = str(r[pi]).strip() if (pi is not None and pi < len(r) and r[pi] is not None) else ""
            if not title or title in skip:
                continue
            # 真商品行判据:price 列可解析成数字;模板说明行(商品名称必须少于…)的价格是非数字
            price = r[pr] if (pr is not None and pr < len(r)) else None
            try:
                cost = float(price)
            except (TypeError, ValueError):
                continue
            d = {"title": title, "cost": cost}
            wb.close()
            return d, table
        wb.close()
        return {}, table
    except Exception:
        return {}, table


def _count_products(table):
    """读真上架表,返回 (去重商品数, SKU行数)。读不到返回 (0,0),不抛。

    同一扫描逻辑:product_name 表头 + price 列可解析成数字才算真商品行。
    上架表按 SKU 铺行(product_name 重复),所以商品数要按标题去重。
    """
    import os
    if not table or not os.path.exists(table):
        return 0, 0
    try:
        import openpyxl
        wb = openpyxl.load_workbook(table, read_only=True)
        ws = wb["Template"] if "Template" in wb.sheetnames else wb.active
        rows = ws.iter_rows(values_only=True)
        header = None
        for r in rows:
            if any(isinstance(c, str) and c.strip() == "product_name" for c in (r or [])):
                header = [str(c) if c is not None else "" for c in r]
                break
        if not header:
            wb.close()
            return 0, 0
        pi = next((i for i, h in enumerate(header) if h.strip() == "product_name"), None)
        pr = next((i for i, h in enumerate(header) if h.strip() == "price"), None)
        skip = {"create_product", "metric", "category_v2", "商品名称", "产品名称",
                "必填", "选填", "商品描述", "产品描述"}
        products: list[str] = []
        sku_rows = 0
        for r in rows:
            if not r or not any(c is not None and str(c).strip() for c in r):
                continue
            title = str(r[pi]).strip() if (pi is not None and pi < len(r) and r[pi] is not None) else ""
            if not title or title in skip:
                continue
            price = r[pr] if (pr is not None and pr < len(r)) else None
            try:
                float(price)
            except (TypeError, ValueError):
                continue
            sku_rows += 1
            if title not in products:
                products.append(title)
        wb.close()
        return len(products), sku_rows
    except Exception:
        return 0, 0


# 中文品类词 → 平台库英文 category(与 app/cleaning.infer_category 产出对齐)
_CN_CATEGORY_TO_EN = {
    "项链": "Necklace", "耳环": "Earrings", "手链": "Bracelet",
    "戒指": "Ring", "发饰": "Hair Accessory", "配饰": "",  # 配饰=不过滤
}
_SITE_LABEL = {"th": "TH店铺", "ph": "PH店铺", "vn": "VN店铺"}
_ACTIVE_LISTING_STATUSES = ["待上架", "处理中", "文案生成中", "图片质检中",
                            "审批中", "上架中"]


def _select_upload(text: str, intent: dict) -> dict:
    """「从采集库按品类选 N 件上架到 X」→ 在上架表(表B)生成待上架行。

    在采集库按 category 选 target 件真实商品(按 id 序;已在上架表有活跃行
    的同 SPU×同店铺 跳过,防重复),每件写一行: SPU/商品ID/标题/分类/店铺站点/
    状态=待上架。轮询器随后自动捡走跑真实流水线。
    没命中 → 如实回「采集库无该品类商品」,不硬凑。
    """
    from ..feishu import bitable
    from ..models import Product
    from ..database import SessionLocal

    market = str(intent.get("market") or "ph").strip().lower()
    if market not in ("th", "ph", "vn"):
        market = "ph"
    site = _SITE_LABEL[market]
    cn_cat = str(intent.get("category") or "").strip()
    en_cat = _CN_CATEGORY_TO_EN.get(cn_cat, "")
    target = int(intent.get("target") or 0)

    # 去重口径:同 SPU×同店铺 已有任何行(含「已上架」)都跳过 —— 只跳过活跃行
    # 会在飞书重投旧消息(实测:旧耳环消息晚到再处理)时把已上架商品再选一遍,
    # 产生重复任务行。任何已存在的行都代表该商品在这家店已被处理/正在处理。
    # 飞书 search 不支持嵌套 or 分组(99992402),先按店铺平铺查该店所有行。
    try:
        site_rows = bitable.list_records([("店铺站点", "is", site)])
        active_spus = {str((r.get("fields") or {}).get("SPU") or "").strip()
                       for r in site_rows if (r.get("fields") or {}).get("SPU")}
        # 商品ID 去重:手动行可能只填了商品ID没有 SPU,按商品ID再挡一层,
        # 保证同商品(同店铺)绝不重复写进上架表。
        active_gids = {str((r.get("fields") or {}).get("商品ID") or "").strip()
                       for r in site_rows if (r.get("fields") or {}).get("商品ID")}
    except Exception as exc:
        active_spus = set()
        active_gids = set()
        print(f"[select_upload] 查上架表已有行失败: {exc}", flush=True)

    db = SessionLocal()
    try:
        q = db.query(Product).filter(Product.active == True).order_by(Product.id)
        if en_cat:
            q = q.filter(Product.category == en_cat)
        products = q.all()
        # 会话存活期物化全部展示字段(防 db.close() 后访问 ORM 属性触发 lazy load):
        # spu/source_goods_id/title_cn/category 是列,image_urls/main_image_url 是 JSON 列,
        # skus 是 relationship —— 全部在 session 内取成纯 dict,后续循环/后台填图线程
        # 零 ORM 访问,彻底断掉 DetachedInstanceError(lazy load 'skus')这类崩溃。
        products = [{
            "spu": p.spu or f"SPU{p.id:06d}",
            "gid": p.source_goods_id,
            "title": (p.title_cn or "")[:200],
            "category": p.category or "",
            "urls": p.image_urls or ([p.main_image_url] if p.main_image_url else []),
            "skus": [{"color": s.color, "style": s.style,
                      "supplier_sku_id": s.supplier_sku_id}
                     for s in p.skus],
        } for p in products]
    finally:
        db.close()
    if not products:
        fields = [("指令", (text or "")[:30]),
                  ("品类", cn_cat or "全部"),
                  ("结果", f"采集库没有「{cn_cat}」分类的商品,未生成上架任务"),
                  ("提示", "先采集入库,选品表/上架表就会自动有数据")]
        sent = fc.deliver_card(fc.approval_card(
            title="上架机器人", color="grey", fields=fields, buttons=[]))
        return {"run_id": None, "mode": "select_upload", "intent": intent,
                "created": 0, "sent": sent}

    created = 0
    skipped = 0
    image_jobs = []
    # 本批次号:轮询按它只捡本次新建的行,表里残留的旧「待上架」行不混批。
    batch_id = f"sel_{time.strftime('%Y%m%d_%H%M%S')}"
    for p in products:
        if target and created >= target:
            break
        if (p["spu"] and p["spu"] in active_spus) or (p["gid"] and p["gid"] in active_gids):
            skipped += 1  # 该商品(SPU/商品ID)在目标店铺已有任务在跑,不重复
            continue
        fields = {
            "SPU": p["spu"],
            "商品ID": p["gid"],
            "标题(中文)": p["title"],
            "分类": p["category"],
            "店铺站点": site,
            "状态": "待上架",
            "任务批次": batch_id,
        }
        try:
            record_id = bitable.create_record(fields)
            if record_id:
                image_jobs.append((record_id, p["urls"], p["skus"]))
            created += 1
        except Exception as exc:
            print(f"[select_upload] 写上架表失败 {p['spu']}: {exc}", flush=True)
    if image_jobs:
        # 新任务行的图片+SKU 后台真实上传填充(几十秒级),审批卡不阻塞
        threading.Thread(target=_fill_listing_images, args=(image_jobs,), daemon=True).start()
    summary = (f"已从采集库选 {created} 件"
               + (f"「{cn_cat}」" if cn_cat else "全部品类")
               + f"生成上架任务({site}),轮询会自动跑")
    if skipped:
        summary += f";跳过已在上架表有任务的 {skipped} 件"
    if not created and skipped:
        summary = f"{site} 上架表已有 {skipped} 件该品类商品在跑,未重复生成"
    fields = [("指令", (text or "")[:30]),
              ("模式", _MODE_LABEL["select_upload"]),
              ("结果", summary)]
    sent = fc.deliver_card(fc.approval_card(
        title="上架机器人", color="blue", fields=fields, buttons=[]))
    return {"run_id": None, "mode": "select_upload", "intent": intent,
            "created": created, "sent": sent}


def _fill_listing_images(jobs):
    """新生成上架任务行的后台图片+SKU 填充(真实上传,失败记日志不阻塞主流程)。"""
    from ..feishu import bitable
    done = 0
    for record_id, urls, skus in jobs:
        try:
            n = bitable.fill_record_images(record_id, urls or [])
            detail = bitable.sku_detail_lines(skus)
            if detail:
                bitable.batch_update(
                    [{"record_id": record_id,
                      "fields": {bitable.SKU_DETAIL_FIELD: detail}}])
            done += 1
            print(f"[select_upload] 填图 {n} 张 + SKU明细(record={record_id})", flush=True)
        except Exception as exc:
            print(f"[select_upload] 填图失败(record={record_id}): {exc}", flush=True)
    print(f"[select_upload] 后台填图/SKU 完成 {done}/{len(jobs)} 行", flush=True)


# ---------------------------------------------------------------- 审图剔除后补位
# 补位选品成本窗口(与 export_platform_to_staging.py 默认 --min-cost/--max-cost 一致)
_COST_WINDOW = (2.0, 40.0)


def _pick_refill_candidates(market, en_cat, count, exclude_gids=()):
    """审图剔除(图片问题)后,从采集库选补位商品。

    口径:Product.active==True + 同品类 + cost_cny_used ∈ _COST_WINDOW
        + 排除同店已有行 SPU(active_spus,防重复上架)+ 排除 exclude_gids(本次批次)。
    返回已物化 dict 列表(照 _select_upload 物化,防 db.close() 后 ORM lazy load 崩溃),
    按 id 序取 count 件。库空返回空列表,由调用方发卡片提示。
    """
    from ..feishu import bitable
    from ..models import Product
    from ..database import SessionLocal

    count = int(count or 0)
    if count <= 0:
        return []
    market = str(market or "").strip().lower()
    site = _SITE_LABEL.get(market, _SITE_LABEL["ph"])
    en_cat = str(en_cat or "").strip()
    exclude = {str(g) for g in (exclude_gids or ())}
    try:
        site_rows = bitable.list_records([("店铺站点", "is", site)])
        active_spus = {str((r.get("fields") or {}).get("SPU") or "").strip()
                       for r in site_rows if (r.get("fields") or {}).get("SPU")}
        active_gids = {str((r.get("fields") or {}).get("商品ID") or "").strip()
                       for r in site_rows if (r.get("fields") or {}).get("商品ID")}
    except Exception as exc:
        active_spus = set()
        active_gids = set()
        print(f"[refill] 查上架表已有行失败: {exc}", flush=True)

    db = SessionLocal()
    try:
        q = db.query(Product).filter(Product.active == True).order_by(Product.id)
        if en_cat:
            q = q.filter(Product.category == en_cat)
        out = []
        for p in q.all():
            if len(out) >= count:
                break
            if not (_COST_WINDOW[0] <= (p.cost_cny_used or 0) <= _COST_WINDOW[1]):
                continue
            gid = p.source_goods_id or f"B{p.id}"
            spu = p.spu or f"SPU{p.id:06d}"
            if (spu and spu in active_spus) or (gid and gid in active_gids):
                continue
            if gid in exclude:
                continue
            out.append({
                "spu": spu,
                "gid": gid,
                "title": (p.title_cn or "")[:200],
                "category": p.category or "",
                "cost": p.cost_cny_used or 0,
                "urls": p.image_urls or ([p.main_image_url] if p.main_image_url else []),
                "skus": [{"color": s.color, "style": s.style,
                          "supplier_sku_id": s.supplier_sku_id}
                         for s in p.skus],
            })
        return out
    finally:
        db.close()


def create_refill_rows(market, en_cat, count, exclude_gids=()):
    """补位:从采集库选 count 件生成表B「待上架」行(新批次 refill_*),轮询会自动捡走。

    只补一轮的触发由调用方(bitable_flow._maybe_refill_after_review)判断;
    这里库空(0 候选)不建行,返回 {"created": 0},由调用方发卡片提示。
    返回 {"created": 实际生成行数, "skipped": 写行失败的件数}。
    """
    from ..feishu import bitable

    count = int(count or 0)
    if count <= 0:
        return {"created": 0, "skipped": 0}
    cands = _pick_refill_candidates(market, en_cat, count, exclude_gids=exclude_gids)
    if not cands:
        print(f"[refill] 无符合条件的补位商品({market},{en_cat or '全品类'}),未建行", flush=True)
        return {"created": 0, "skipped": 0}
    market = str(market or "").strip().lower()
    site = _SITE_LABEL.get(market, _SITE_LABEL["ph"])
    batch_id = f"refill_{time.strftime('%Y%m%d_%H%M%S')}"
    created = 0
    skipped = 0
    image_jobs = []
    for p in cands:
        if created >= count:
            break
        fields = {
            "SPU": p["spu"],
            "商品ID": p["gid"],
            "标题(中文)": p["title"],
            "分类": p["category"],
            "店铺站点": site,
            "状态": "待上架",
            "任务批次": batch_id,
        }
        try:
            record_id = bitable.create_record(fields)
            if record_id:
                image_jobs.append((record_id, p["urls"], p["skus"]))
            created += 1
        except Exception as exc:
            print(f"[refill] 写补位行失败 {p['spu']}: {exc}", flush=True)
            skipped += 1
    if image_jobs:
        threading.Thread(target=_fill_listing_images, args=(image_jobs,), daemon=True).start()
    print(f"[refill] 补位 {created}/{count} 件({site},{en_cat or '全品类'},batch={batch_id})",
          flush=True)
    return {"created": created, "skipped": skipped}


def handle_instruction(text):
    """群指令 → interpret agent 理解意图 → 按模式分流 → 采集→制表→审图 → 审批卡 → 发飞书群。

    mode 分流:
      stop          不跑流水线,回"已理解,不执行"卡
      capture_only  只跑 collect,回采集结果卡(不制表/不审图/不上架)
      review_only   跑 collect→table→review,审批通过也跳过上架(只审不传)
      upload(默认)  跑 collect→table→review→审批→真上架

    全真实:collect/table/review 都用 claude -p + skills 真跑脚本;
    table 输出的真表路径喂给 review(审图跑在真表上),并存进状态供上架用。
    任一环节失败不中断,失败信息带进审批卡(卡上标注)。
    """
    intent = _interpret(text)
    mode = intent["mode"]
    run_id = uuid.uuid4().hex[:8]
    params = {**intent, "run_id": run_id}

    # 明确不执行 / 无法理解:直接回卡,不建 run、不跑任何 stage
    if mode in ("stop", "reject"):
        if mode == "reject":
            fields = [("指令", (text or "")[:30]),
                      ("理解", "这条不是上架/采集/审图指令,我没法执行"),
                      ("模式", _MODE_LABEL["reject"])]
        else:
            fields = [("指令", (text or "")[:30]),
                      ("理解", intent.get("note") or "不执行"),
                      ("模式", _MODE_LABEL["stop"])]
        sent = fc.deliver_card(fc.approval_card(
            title="上架机器人", color="grey", fields=fields, buttons=[]))
        return {"run_id": None, "mode": mode, "intent": intent, "sent": sent}

    # 从采集库按品类选商品 → 生成上架表行,由轮询跑流水线(不建 run)
    if mode == "select_upload":
        return _select_upload(text, intent)

    create(run_id, params=params)
    stages = {}
    p = {**params}
    # 采集 75 件实测约 12 分钟(720s+);采集支持自检回补(不够目标继续补采),时长更长——
    # 600s 曾导致误报"采集失败",先提到 1200,补采机制后提到 1800 给足缓冲
    timeouts = {"collect": 1800, "table": 420, "review": 420}

    # 只采集:跑完 collect 回结果卡就结束(market 已从意图贯穿 collect 提示词)
    if mode == "capture_only":
        stages["collect"] = tasks.run_stage("collect", p, timeout_s=timeouts.get("collect", 420))
        collect = _stage_dict(stages.get("collect"))
        captured = int(collect.get("captured") or 0)
        with _lock:
            st = _STATE.get(run_id)
            if st:
                st["candidates"] = captured
                st["stages"] = {k: _stage_dict(v) for k, v in stages.items()}
        card_data = build_card(run_id, stages, candidates=captured)
        sent = fc.deliver_card(fc.approval_card(**card_data))
        return {"run_id": run_id, "stages": stages, "mode": mode, "card": card_data, "sent": sent}

    # 采集入库:collect(真采集)→ 确定性桥接脚本写平台库 → 回结果卡(不制表/不审图/不上架)
    if mode == "capture_db":
        _collect_started = time.time()
        stages["collect"] = tasks.run_stage("collect", p, timeout_s=timeouts.get("collect", 420))
        collect = _stage_dict(stages.get("collect"))
        if _stage_err(stages.get("collect")):
            # 采集失败不直接跳过:claude -p 收尾响应失败(unrecognized_model 等网关偶发)
            # 不代表采集没完成。找本轮 collect 期间新出现的 1688 run,有真数据就继续入库;
            # 找不到才判失败(避免把历史 run 灌进库)。
            rescued_dir = _find_recent_collect_run(after_ts=_collect_started)
            if rescued_dir:
                br = _run_bridge_deterministic(rescued_dir, p.get("market", "ph"))
                if isinstance(br, dict) and isinstance(br.get("stage_result"), dict):
                    br["stage_result"]["rescued"] = True
                stages["bridge_to_db"] = br
            else:
                stages["bridge_to_db"] = {"ok": False, "stage_result": {},
                                          "error": "采集未成功,跳过入库"}
        else:
            stages["bridge_to_db"] = _run_bridge_deterministic(
                str(collect.get("run_dir") or ""), p.get("market", "ph"))
        with _lock:
            st = _STATE.get(run_id)
            if st:
                st["stages"] = {k: _stage_dict(v) for k, v in stages.items()}
        card_data = build_bridge_card(run_id, stages)
        sent = fc.deliver_card(fc.approval_card(**card_data))
        return {"run_id": run_id, "stages": stages, "mode": mode, "card": card_data, "sent": sent}

    # upload / review_only:跑完整采集→制表→审图,review_only 时 mode 记入状态,
    # transition 通过后据此跳过上架
    for s in ("collect", "table"):
        stages[s] = tasks.run_stage(s, p, timeout_s=timeouts.get(s, 420))

    # 真编排:table 生成的表喂给 review;并从真表读首行做 RAG 抽查样例
    # agent 可能把 staging csv 也塞进 out_tables,审图只认 xlsx 上架表,这里过滤
    tables = [str(t) for t in (_stage_dict(stages.get("table")).get("out_tables") or [])
              if str(t).lower().endswith(".xlsx")]
    sample, sample_path = {}, (tables[0] if tables else "")
    if sample_path:
        sample, _ = _first_row(sample_path)
    run_dir = os.path.dirname(os.path.dirname(sample_path)) if sample_path else ""
    # 成本用平台库真值(表里是售价);窗口 [2,40] 由 export 已保证
    cost = _db_sample_cost(params.get("market")) or 8
    # RAG 规则层按中文标题建词表,上架表里的 product_name 是英文;
    # 用库里的真实中文标题抽查,避免 category 恒判失败(误报)
    cn_title = _db_sample_title(params.get("market"))
    p = {**p, "tables": tables, "run_dir": run_dir,
         "sample_title": cn_title or sample.get("title") or params.get("category", "项链"),
         "cost": cost}
    stages["review"] = tasks.run_stage("review", p, timeout_s=timeouts.get("review", 420))

    # 审图生成了 *_zhfiltered.xlsx(去掉含汉字/尺寸FAIL图的过滤副本)时,上架用过滤副本。
    # 判定不看 agent 回传,直接 glob 源表同目录 —— 确定性,不依赖解析。
    filtered_tables = []
    for t in tables:
        cand = os.path.join(os.path.dirname(t),
                            os.path.splitext(os.path.basename(t))[0] + "_zhfiltered.xlsx")
        if os.path.isfile(cand):
            filtered_tables.append(cand)
    upload_tables = filtered_tables or tables

    # 候选数从真表数出去重商品数(表按 SKU 铺行,product_name 重复)
    candidates, _ = (_count_products(tables[0]) if tables else (0, 0))

    with _lock:
        st = _STATE.get(run_id)
        if st:
            # tables=全量原表(「通过全部」用);tables_ok=审图过滤副本
            # (仅通过审图OK 用,已剔除无可用图的阻塞行)。transition 按 decision 选。
            st["params"] = {**st["params"], "tables": tables,
                            "tables_ok": upload_tables, "run_dir": run_dir}
            st["candidates"] = candidates
            st["stages"] = {k: _stage_dict(v) for k, v in stages.items()}

    card_data = build_card(run_id, stages, candidates=candidates)
    # 有应用配置走 API(按钮真回调);否则退回 webhook(只能发)
    sent = fc.deliver_card(fc.approval_card(**card_data))
    return {"run_id": run_id, "stages": stages, "mode": mode, "card": card_data, "sent": sent}


def _stage_dict(stage_res):
    """取环节结果里的 stage_result;非 dict(字符串/异常/缺失)兜底空 dict,不崩编排。"""
    if isinstance(stage_res, dict):
        sr = stage_res.get("stage_result")
        return sr if isinstance(sr, dict) else {}
    return {}


def _stage_err(stage_res):
    """取环节顶层/内部的 error;无返回 None。_stage_dict 会丢掉顶层 error,这里补查。"""
    if not isinstance(stage_res, dict):
        return None
    err = stage_res.get("error")
    if err:
        return err
    sr = stage_res.get("stage_result")
    if isinstance(sr, dict) and sr.get("error"):
        return sr["error"]
    return None


def _find_recent_collect_run(after_ts=None, fresh_seconds=1800):
    """collect 收尾响应失败时的兜底:找本轮 collect 期间新出现的 1688 run 目录。

    claude -p 可能在采集已真实完成(PowerShell 写完 captures/)之后、收尾生成 JSON 时
    被 deepseek 网关偶发拒绝(unrecognized_model),进程返回非零 → bridge 判失败。
    此时 run 目录是新鲜的(st_mtime >= after_ts)、采集数据是真实完成的,可以继续入库,
    避免"采集其实成功却因收尾响应失败被整批丢弃"。

    只认本轮 collect 开始之后新出现的目录(after_ts 过滤),找不到(采集真没跑成)返回 "",
    不会拿历史 run 兜底灌库。
    """
    try:
        from ..config import BASE_DIR
        pkg = os.environ.get(
            "ROSEEK_PKG_DIR",
            str(BASE_DIR.parent / "RoseSeek_TikTokShop_AI_Localized_20260809"),
        )
        base = os.path.join(pkg, "runs")
        if not os.path.isdir(base):
            return ""
        now = time.time()
        newest = None
        for name in os.listdir(base):
            p = os.path.join(base, name)
            if not (name.startswith("1688_accessories_") and os.path.isdir(p)):
                continue
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            if after_ts is not None and mtime < after_ts:
                continue          # 不是本轮 collect 产生的,不碰
            if now - mtime > fresh_seconds:
                continue          # 太久没更新,不兜底
            if newest is None or mtime > newest[1]:
                newest = (p, mtime)
        return newest[0] if newest else ""
    except Exception:
        return ""


def _run_bridge_deterministic(run_dir, market):
    """确定性跑 1688 采集入库脚本(不经 LLM,DB 写入是源头事实)。

    直接 subprocess 调 tools/import_1688_captures_to_db.py,解析它打印的真实 JSON。
    脚本找不到 run 目录时自己会回退到最新一个 1688 run。
    """
    from ..config import BASE_DIR
    script = os.path.join(str(BASE_DIR), "tools", "import_1688_captures_to_db.py")
    if not os.path.exists(script):
        return {"ok": False, "stage": "bridge_to_db", "stage_result": {},
                "error": f"桥接脚本缺失: {script}"}
    try:
        proc = subprocess.run(
            [sys.executable, script, "--run-dir", run_dir, "--market", str(market or "ph")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "bridge_to_db", "stage_result": {},
                "error": "采集入库超时(300s)"}
    err = (proc.stderr or "").strip()[:400]
    sr = {}
    # 取 stdout 里最后一个能解析的 JSON 行(脚本只打印一行结果 JSON)
    for line in reversed((proc.stdout or "").splitlines()):
        try:
            obj = json.loads(line.strip())
            if isinstance(obj, dict):
                sr = obj
                break
        except Exception:
            continue
    if not sr and proc.returncode != 0:
        return {"ok": False, "stage": "bridge_to_db", "stage_result": {},
                "error": err or "采集入库脚本失败(无 JSON 输出)"}
    return {"ok": proc.returncode == 0, "stage": "bridge_to_db", "stage_result": sr,
            "error": "" if proc.returncode == 0 else err}


def build_bridge_card(run_id, stages):
    """采集入库结果卡:真实件数 + 失败如实标注(不掺假,不粉饰)。"""
    collect = _stage_dict(stages.get("collect"))
    br = _stage_dict(stages.get("bridge_to_db"))
    captured = int(collect.get("captured") or 0)
    imported = int(br.get("imported") or 0)
    already = int(br.get("already") or 0)
    skipped = int(br.get("skipped") or 0)
    cost_filtered = int(br.get("cost_filtered") or 0)
    errs = br.get("errors") or []
    fields = [("模式", "采集入库"),
              ("采集件数", str(captured)),
              ("入库件数", str(imported)),
              ("已存在", str(already)),
              ("跳过", str(skipped)),
              ("run_id", run_id)]
    if cost_filtered:
        bounds = br.get("cost_bounds") or {}
        lo, hi = float(bounds.get("min") or 0), float(bounds.get("max") or 0)
        if lo > 0 and hi > 0:
            label = f"成本过滤(不在 {lo:g}~{hi:g}元)"
        elif hi > 0:
            label = f"成本过滤(>{hi:g}元)"
        else:
            label = "成本过滤"
        fields.insert(-1, (label, str(cost_filtered)))
    notes = []
    rescued = bool(br.get("rescued"))
    for name, label in (("collect", "采集"), ("bridge_to_db", "入库")):
        err = _stage_err(stages.get(name))
        if err:
            if name == "collect" and rescued:
                # 兜底入库:采集数据真实完成,只是收尾响应失败,不标"采集失败"
                notes.append("采集收尾响应异常,已按新鲜采集数据入库")
            else:
                notes.append(f"{label}失败:{str(err)[:60]}")
    if errs:
        notes.append("入库失败: " + ", ".join(
            f"{e.get('goods_id')}={str(e.get('error'))[:40]}" for e in errs[:3]))
    for n in notes:
        fields.append(("⚠️", n))
    color = "green" if imported and not errs else "red"
    return {"title": f"采集入库 #{run_id}", "color": color, "fields": fields,
            "buttons": [], "values": {}}


def build_card(run_id, stages, candidates=None):
    """从各环节结果组装审批卡字段与按钮。

    候选数只信真实数据:优先真表的去重商品数(candidates,编排层从 xlsx 数出),
    其次采集入库件数(collect.captured),再次真表数量;全空才 "?"。
    注意 review 环节 agent 只会原样回 schema 里的 "tables":[],不能拿它算。"""
    collect = _stage_dict(stages.get("collect"))
    table = _stage_dict(stages.get("table"))
    review = _stage_dict(stages.get("review"))
    st = get(run_id) or {}
    params = st.get("params") or {}
    market = str(params.get("market") or "ph").lower()
    mode = str(params.get("mode") or "upload")
    xlsx = [str(t) for t in (table.get("out_tables") or [])
            if str(t).lower().endswith(".xlsx")]
    image_fail = int(review.get("image_fail") or 0)
    rule_fail = int(review.get("rule_fail") or 0)
    table_fail = int(review.get("table_fail") or 0)
    captured = (int(candidates) if candidates else 0) \
        or int(collect.get("captured") or 0) or len(xlsx) or "?"

    # 环节失败如实标注在卡上(采集挂了候选仍可来自平台库,但卡上要说明)
    notes = []
    for name, label in (("collect", "采集"), ("table", "制表"), ("review", "审图")):
        err = _stage_err(stages.get(name))
        if err:
            notes.append(f"{label}失败:{str(err)[:60]}")

    recommend = "驳回" if (image_fail or rule_fail or table_fail) else "通过全部"
    color = "red" if recommend == "驳回" else "blue"
    buttons = ["通过全部", "仅通过审图OK", "驳回"]
    values = {b: {"action": {"通过全部": "approve_all",
                             "仅通过审图OK": "approve_ok",
                             "驳回": "reject"}[b],
                  "run_id": run_id}
              for b in buttons}
    # 首行展示 agent 理解结果:市场店铺/模式/数量(直击"内容没被 agent 理解"的抱怨)
    target = int(params.get("target") or 0)
    mode_label = _MODE_LABEL.get(mode, mode)
    market_label = _MARKET_LABEL.get(market, market.upper())
    target_txt = str(target) if target else "全部候选"
    fields = [("市场/店铺", market_label),
              ("模式", mode_label),
              ("目标数量", target_txt)]
    if params.get("note"):
        fields.append(("指令理解", str(params["note"])[:40]))
    fields += [("候选数", str(captured)),
               ("成本区间", "¥2-40"),
               ("审图FAIL", str(image_fail)),
               ("违禁命中", str(rule_fail)),
               ("表规FAIL", str(table_fail)),
               ("run_id", run_id)]
    # 汉字图被自动过滤的件数如实标注;上架用过滤副本时提示,避免运营以为上的是原表
    zh_flagged = int(review.get("zh_flagged") or 0)
    if zh_flagged:
        fields.append(("汉字图过滤", f"{zh_flagged} 张(审图已移除,只留商品图)"))
    filtered = [str(t) for t in (review.get("filtered_tables") or [])]
    if filtered:
        fields.append(("上架表", "已用过滤副本 *_zhfiltered.xlsx"))
    for n in notes:
        fields.append(("⚠️", n))
    return {
        "title": f"选品审批 #{run_id}",
        "fields": fields,
        "buttons": buttons,
        "values": values,
        "color": color,
        "recommend": recommend,
    }
