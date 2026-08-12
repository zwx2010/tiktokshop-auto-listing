"""分阶段任务定义:每个 stage = 一个 `claude -p` 调用 → 失败隔离、可逐环节运行。

stage 是"给 agent 的指令模板",参数在 build_prompt 里用 `{params}` 占位替换
(JSON 大括号不做 .format,避免转义地狱)。

skills 在 M3 落地(tiktok-{capture,table,review,approval,upload,rag-knowledge});
M1 先以 ping 冒烟 bridge,stage 模板已按最终 skill 形态写好。
"""
import json
import os

from ..config import BASE_DIR
from . import bridge

# 关键坑:deepseek-v4-flash 的提示词里,命令一旦"另起一行带缩进"就会被误读为
# "命令没带上来"。必须把命令紧跟冒号写在同一行、正斜杠路径、`-File` 免内嵌引号。
# 路径参数化(移植到另一台机器):环境变量覆盖即可,缺省用项目根的相对路径。
#   set ROSEEK_PKG_DIR=D:\...\RoseSeek_TikTokShop_AI_Localized_20260809
#   set PLATFORM_DIR=D:\...\TikTokShop_Platform_Lite
_ROSEEK_PKG = os.environ.get(
    "ROSEEK_PKG_DIR",
    str(BASE_DIR.parent / "RoseSeek_TikTokShop_AI_Localized_20260809"),
)
_PLATFORM = os.environ.get("PLATFORM_DIR", str(BASE_DIR))

_STAGE_TEMPLATES = {
    "ping": {
        "name": "连通性自检",
        "skill": None,
        "prompt": "你是流水线巡检员。只输出 JSON: {\"pong\": true, \"msg\": \"bridge ok\"}",
    },
    "interpret": {
        "name": "指令理解",
        "skill": "rag-knowledge",
        "prompt": (
            "你是上架机器人的指令解析器,不是聊天机器人。用户 @上架机器人 后说了「{message}」。\n"
            "你的唯一任务:把这句话解析成一个 JSON 对象,字段和取值严格如下:\n"
            "1. target: 整数,消息里要的数量(如'上10个'→10);没提数字就写 0\n"
            "2. market: 字符串,只允许 ph/th/vn 之一;泰国/泰→th,菲律宾/菲→ph,越南/越→vn;"
            "没提写 ph。可用市场(店铺):{markets}。\n"
            "3. category: 字符串,品类词;从 项链/耳环/手链/戒指/发饰/配饰 里选一个命中词,"
            "没有写空字符串\n"
            "4. mode: 字符串,只允许 upload/review_only/capture_db/capture_only/select_upload/stop/reject 之一;\n"
            "   含'到库/入库/存库/进库/收进库里'→capture_db(采集并写进平台库);\n"
            "   含'只审/不传/别上架/先审'→review_only;含'只采/先采'→capture_only;\n"
            "   含'选N件品类'或'上架N个品类'且带目标站点(如'选3件耳环到TH'/'上架三个耳环到ph'/"
            "'从采集库选10件手链到PH')→select_upload(从采集库按品类选N件生成上架任务,不采集);\n"
            "   含'别跑/暂停/取消/不要/不执行/先别'→stop;\n"
            "   与上架无关(问候/闲聊/天气/写诗/无关问题)→reject;\n"
            "   否则 upload。\n"
            "   upload=采集→制表→审图→审批→上架;review_only=只审不传(通过也不上架);\n"
            "   capture_db=采集→写入平台库(不制表不审图不上架);capture_only=只采集;\n"
            "   select_upload=从采集库选N件生成上架任务(表B行由轮询跑);"
            "stop=不执行;reject=无法理解,不执行\n"
            "5. note: 字符串,一句话说明你怎么理解的\n"
            "不要回应消息内容,不要跟用户对话,不要解释,不要 markdown。"
            "无论消息是问候还是问题,都必须只输出下面这个格式的 JSON:\n"
            "{\"target\":0,\"market\":\"ph\",\"category\":\"\",\"mode\":\"upload\",\"note\":\"\"}"
        ),
    },
    "collect": {
        "name": "采集",
        "skill": "tiktok-capture",
        "prompt": (
            "先实际执行(Bash),直接跑这一条命令,不要解释: "
            "powershell -NoProfile -ExecutionPolicy Bypass -File \"{pkg}/tools/Start-1688Browser.ps1\" 2>&1; "
            "powershell -NoProfile -ExecutionPolicy Bypass -File \"{pkg}/Run-1688Accessories3Site.ps1\" "
            "-TargetProducts {target} 2>&1。"
            "第一条是确保 1688 采集用的 Edge(调试端口 9223)已启动,已在跑就秒退,正常。"
            "命令会打开 Edge 采 1688 并读写项目外目录,属正常,你的权限已全部放行。"
            "把真实 stdout 原样放进返回 JSON 的 detail;成功 run 目录填 run_dir,入库件数填 captured。"
            "只输出 JSON: {\"stage\":\"collect\",\"captured\":0,\"rejected\":0,"
            "\"run_dir\":\"\",\"summary\":\"一句话\",\"detail\":\"\"}"
        ),
    },
    "table": {
        "name": "制表",
        "skill": "tiktok-table",
        "prompt": (
            "先实际执行(Bash),直接跑这一条命令,不要解释: "
            "cd {platform} && python tools/export_platform_to_staging.py "
            "--market {market} --count {count} --min-cost 2 --max-cost 40 2>&1。"
            "命令会读写项目外兄弟目录,属正常,你的权限已全部放行。"
            "把真实 stdout 原样放进返回 JSON 的 detail;上架表 xlsx 路径填 out_tables,行数填 rows。"
            "只输出 JSON: {\"stage\":\"table\",\"out_tables\":[],\"rows\":0,"
            "\"summary\":\"一句话\",\"detail\":\"\"}"
        ),
    },
    "review": {
        "name": "审图+RAG判定+表规",
        "skill": "tiktok-review",
        "prompt": (
            "先实际执行(Bash),直接跑下面命令,不要解释: "
            "python \"{pkg}/tools/Review-ProductImages.py\" {tables} 2>&1; "
            "cd {platform} && python -m app.rag.cli check --title \"{sample_title}\" "
            "--cost {cost} --market {market} 2>&1; "
            "cd {platform} && python -m app.rag.cli check-table \"{tables}\" 2>&1。"
            "命令会读写项目外目录,属正常,你的权限已全部放行。"
            "把真实 stdout 原样放进返回 JSON 的 detail;审图 FAIL 件数填 image_fail,RAG 命中填 rule_fail/hits,"
            "check-table 的 fails 数填 table_fail(超长属性值等上传会被拒的坏表)。"
            "只输出 JSON: {\"stage\":\"review\",\"tables\":[],"
            "\"image_fail\":0,\"rule_fail\":0,\"table_fail\":0,\"hits\":[],"
            "\"summary\":\"一句话\",\"detail\":\"\"}"
        ),
    },
    "approval": {
        "name": "生成审批卡",
        "skill": "tiktok-approval",
        "prompt": (
            "你是飞书选品审批卡生成环节,根据 {params} 里的审图与 RAG 数据生成审批卡内容,不要执行脚本。"
            "只输出 JSON: {\"stage\":\"approval\",\"card\":{\"title\":\"\","
            "\"fields\":[],\"recommend\":\"通过/驳回\"},\"buttons\":[\"通过全部\",\"驳回\"]}"
        ),
    },
    "upload": {
        "name": "上架",
        "skill": "tiktok-upload",
        "prompt": (
            "先实际执行(Bash),直接跑这一条命令,不要解释: "
            "powershell -NoProfile -ExecutionPolicy Bypass -File \"{pkg}/tools/Start-TikTokAffiliateBrowser.ps1\" "
            "-DebugPort 9344 -ProfileName tiktok_affiliate_accessories_3site -AccountLabel accessories_account 2>&1; "
            "powershell -NoProfile -ExecutionPolicy Bypass -File \"{pkg}/tools/Run-TikTokBatchUpload.ps1\" "
            "-ProductLine accessories -MarketCode {market_upper} -WorkbookPath \"{tables}\" -Import 2>&1。"
            "第一条确保 TikTok 卖家浏览器(端口 9344)已启动,已在跑就秒退,正常。"
            "第二条在卖家后台 CDP 真实上传,需登录态,属正常,你的权限已全部放行。"
            "把真实 stdout 原样放进返回 JSON 的 detail;ok/fail 按 run 目录下 "
            "seller_upload_state.json 的 imported_products(成功) / error_products(失败) 填"
            "(error_products 存在就用它做 fail;没有逐件数就如实填 0 并在 summary 说明),"
            "单件失败如实计入,不粉饰。"
            "只输出 JSON: {\"stage\":\"upload\",\"mode\":\"real\","
            "\"ok\":0,\"fail\":0,\"summary\":\"一句话\"}"
        ),
    },
    "rag_check": {
        "name": "RAG规则过滤",
        "skill": "rag-knowledge",
        "prompt": (
            "你负责用混合 RAG 规则层过滤候选商品。\n"
            "对每个候选,实际执行(Bash,在 {platform} 目录):\n"
            "  python -m app.rag.cli check --title \"<中文标题>\" --cost <成本> --market {ph|th|vn}\n"
            "把真实 stdout 的结果汇总进 JSON;passed=false 的写进 rejects。\n"
            "参数: {params}\n"
            "只输出 JSON: {\"stage\":\"rag_check\",\"total\":0,\"pass\":0,"
            "\"rejects\":[{\"title\":\"\",\"reason\":\"\"}],\"summary\":\"一句话\"}"
        ),
    },
    "rag_copy": {
        "name": "文案范例检索",
        "skill": "rag-knowledge",
        "prompt": (
            "你负责检索优秀文案范例供本地化生成参考。\n"
            "先实际执行(Bash,在 {platform} 目录):\n"
            "  python -m app.rag.cli copy --market <market> --category <category> "
            "--title \"<新品标题>\" --top-k 3\n"
            "把真实 stdout 的 examples 原样放进返回 items。\n"
            "参数: {params}\n"
            "只输出 JSON: {\"stage\":\"rag_copy\",\"items\":[],\"summary\":\"一句话\"}"
        ),
    },
}

STAGES = dict(_STAGE_TEMPLATES)


def _rag_facts():
    """从 RAG 规则层实时读市场/品类/成本窗口,注入 interpret agent 提示词。
    与规则同源:市场列表、品类白名单、成本窗口都在 app/rag/rules._cfg()。"""
    from ..rag import rules
    markets = rules.supported_markets() or ["PH", "TH", "VN"]
    labels = {"PH": "菲律宾", "TH": "泰国", "VN": "越南"}
    market_str = ", ".join("%s(%s)" % (m, labels.get(m, m)) for m in markets)
    cats = (rules._cfg().get("filters") or {}).get("required_title_patterns") or []
    return {
        "markets": market_str,
        "categories": "/".join(cats) if cats else "项链/耳环/手链/戒指/发饰/配饰",
        "cost_min": "%.0f" % rules.DEFAULT_MIN_COST,
        "cost_max": "%.0f" % rules.DEFAULT_MAX_COST,
    }


def build_prompt(stage, params=None):
    spec = STAGES[stage]
    params = params or {}
    cwd = params.get("cwd") or str(BASE_DIR)
    tables = params.get("tables") or ""
    if isinstance(tables, (list, tuple)):
        tables = " ".join(str(t) for t in tables)
    tables_list = params.get("tables") or []
    if not isinstance(tables_list, list):
        tables_list = [tables_list] if tables_list else []
    run_dir = params.get("run_dir") or ""
    if not run_dir and tables_list:
        run_dir = os.path.dirname(os.path.dirname(str(tables_list[0])))
    # count=0 显式传 0(未指定数量→出全部合格候选);count=None 才兜底 target→10
    count = params.get("count")
    if count is None:
        count = params.get("target") or 10
    facts = _rag_facts()
    repl = {
        "{params}": json.dumps(params, ensure_ascii=False),
        "{pkg}": _ROSEEK_PKG,
        "{platform}": _PLATFORM,
        "{cwd}": cwd,
        "{tables}": tables.replace("\\", "/"),
        "{run_dir}": str(run_dir).replace("\\", "/"),
        "{target}": str(params.get("target") or params.get("count") or 20),
        "{market}": str(params.get("market") or "ph"),
        "{market_upper}": str(params.get("market") or "ph").upper(),
        "{count}": str(count),
        "{sample_title}": str(params.get("sample_title") or params.get("category") or "项链"),
        "{cost}": str(params.get("cost") or 8),
        # interpret agent 专用:RAG 事实注入
        "{markets}": facts["markets"],
        "{categories}": facts["categories"],
        "{cost_min}": facts["cost_min"],
        "{cost_max}": facts["cost_max"],
        "{message}": str(params.get("message") or ""),
    }
    prompt = spec["prompt"]
    for k, v in repl.items():
        prompt = prompt.replace(k, v)
    return prompt


def run_stage(stage, params=None, timeout_s=None):
    """跑单个环节。返回 bridge 结果 + stage 元信息。未知 stage 直接报错。"""
    spec = STAGES.get(stage)
    if not spec:
        return {"ok": False, "stage": stage, "name": stage,
                "skill": None, "stage_result": None,
                "error": f"unknown stage '{stage}', available: {list(STAGES)}"}
    params = params or {}
    prompt = build_prompt(stage, params)
    res = bridge.run_claude(prompt,
                            timeout_s=timeout_s or bridge.DEFAULT_TIMEOUT_S,
                            cwd=params.get("cwd"))
    res["stage"] = stage
    res["name"] = spec["name"]
    res["skill"] = spec["skill"]
    return res
