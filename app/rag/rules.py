"""规则层:结构化过滤(采集/选品的硬性要求)。

把散落在配置里的规则收拢成可查询对象:
- 价格: 成本窗口 [DEFAULT_MIN_COST, DEFAULT_MAX_COST](对齐流水线默认 2–40)
- 违禁: brand_filters + listing_filters 的 blocked_title_patterns + audience 的 blocked 词
- 品类: 标题命中 required_title_patterns 白名单、避开 blocked(复用 cleaning.infer_category)
- 风格: audience 的 preferred 加分词(软性,不作为硬门槛)

所有规则文件均从工作流包 config/ 读取(带 BOM 容错)。
"""
import re
from functools import lru_cache

from .io import read_json, workflow_dir

DEFAULT_MIN_COST = 2.0
DEFAULT_MAX_COST = 40.0


@lru_cache(maxsize=1)
def _cfg():
    """懒加载规则配置。规则文件在工作流包,定价/本地化在平台配置目录。"""
    from ..config import CONFIG_DIR
    wd = workflow_dir()

    def load(name, base):
        p = base / name
        return read_json(p) if p.is_file() else {}

    return {
        "brand": load("brand_filters.json", wd / "config"),
        "filters": load("listing_filters_accessories.json", wd / "config"),
        "audience": load("listing_audience_accessories.json", wd / "config"),
        "pricing": load("market_pricing.json", CONFIG_DIR),
        "localization": load("market_listing_localization.json", CONFIG_DIR),
        # 上传反学库:把 TikTok 真实失败原因(如"属性值名称不应超过50个字符")
        # 落成规则,上架前用 check-table 拦坏表。与 pricing 同目录、同加载心智。
        "upload": load("upload_learning.json", CONFIG_DIR),
    }


def check_price(cost_cny, min_cost=None, max_cost=None):
    """成本窗口检查。无成本输入时跳过(返回 passed=True,detail 注明)。"""
    mn = DEFAULT_MIN_COST if min_cost is None else min_cost
    mx = DEFAULT_MAX_COST if max_cost is None else max_cost
    if cost_cny is None:
        return {"name": "price", "passed": True,
                "detail": "no cost given (skipped)", "cost": None}
    ok = mn <= cost_cny <= mx
    return {"name": "price", "passed": ok,
            "detail": "cost ¥%.2f within [%.1f, %.1f]" % (cost_cny, mn, mx),
            "cost": cost_cny}


def check_banned(title):
    """违禁词/侵权词检查:品牌黑名单 + 标题黑名单 + 人群黑名单。"""
    cfg = _cfg()
    t = (title or "").strip().lower()
    groups = [
        ("brand", cfg["brand"].get("blocked_brand_patterns", [])),
        ("brand_context", cfg["brand"].get("blocked_brand_context_patterns", [])),
        ("title", cfg["filters"].get("blocked_title_patterns", [])),
        ("audience", cfg["audience"].get("blocked_title_patterns", [])),
    ]
    hits = []
    for group, pats in groups:
        for p in pats:
            if p and p.lower() in t:
                hits.append({"group": group, "term": p})
    detail = ", ".join(h["term"] for h in hits[:6]) if hits else "no banned hits"
    return {"name": "banned", "passed": len(hits) == 0,
            "detail": "%d hits: %s" % (len(hits), detail), "hits": hits[:8]}


def check_category(title):
    """品类白/黑名单:必须命中 ≥1 个 required,不得命中 blocked。"""
    from ..cleaning import infer_category
    cfg = _cfg()
    t = (title or "").strip()
    required = cfg["filters"].get("required_title_patterns", [])
    blocked = cfg["filters"].get("blocked_title_patterns", [])
    req_hits = [p for p in required if p and p.lower() in t.lower()]
    block_hits = [p for p in blocked if p and p.lower() in t.lower()]
    ok = bool(not block_hits and req_hits)
    return {"name": "category", "passed": ok, "category": infer_category(t),
            "required_hits": req_hits[:5], "blocked_hits": block_hits[:5],
            "detail": "cat=%s, required=%s, blocked=%s"
                      % (infer_category(t), req_hits[:3], block_hits[:3] or "无")}


def check_style(title):
    """人群风格偏好:命中 preferred 加分(软性),命中 blocked 即降级。"""
    cfg = _cfg()
    t = (title or "").lower()
    pref = [p for p in cfg["audience"].get("preferred_title_patterns", [])
            if p and p.lower() in t]
    block = [p for p in cfg["audience"].get("blocked_title_patterns", [])
             if p and p.lower() in t]
    return {"name": "style", "passed": not block, "soft": True,
            "preferred_hits": pref, "blocked_hits": block,
            "detail": "preferred=%s blocked=%s" % (pref[:4] or "无", block[:4] or "无")}


def evaluate_product(title_cn, category=None, cost_cny=None, market=None,
                     min_cost=None, max_cost=None):
    """对单个商品跑全部硬性规则,返回 {passed, category, market, rules, summary}。

    硬门槛:price / banned / category;style 是软性信息(不卡 passed)。
    """
    rules = [
        check_price(cost_cny, min_cost, max_cost),
        check_banned(title_cn),
        check_category(title_cn),
        check_style(title_cn),
    ]
    passed = all(r["passed"] for r in rules if not r.get("soft"))
    summary = "; ".join(
        "%s:%s" % (r["name"], "OK" if r["passed"] else "FAIL-" + r["detail"])
        for r in rules)
    return {"passed": passed,
            "category": rules[2].get("category"),
            "market": market,
            "rules": rules,
            "summary": summary}


def upload_spec_rules():
    """上传反学规则(upload_learning.json 的 rules 数组)。"""
    return _cfg()["upload"].get("rules", [])


# 模板属性列 → 反学规则列归组:property_value_1/_2 → property_value,依此类推
_COLUMN_GROUP = ("property_name", "property_value", "product_name")


def _upload_group(column):
    """把模板列名归到反学规则列组;不认识返回 None(该列不校验)。"""
    col = (column or "").strip().lower()
    for g in _COLUMN_GROUP:
        if col == g or col.startswith(g + "_"):
            return g
    return None


def check_upload_spec(value, column):
    """上传规格检查(反学规则):property_value≤50 / property_name≤20 / product_name≤255。

    列归组后按 upload_learning.json 里 kind=max_length 的规则校验。
    返回 {name:"upload_spec", passed, column, max_len, rule_id, tiktok_error, detail}。
    """
    group = _upload_group(column)
    if group is None:
        return {"name": "upload_spec", "passed": True,
                "detail": "unknown column %r (skipped)" % (column,)}
    rule = next((r for r in upload_spec_rules()
                 if r.get("column") == group and r.get("kind") == "max_length"), None)
    if not rule:
        return {"name": "upload_spec", "passed": True,
                "detail": "no max_length rule for %s" % group}
    max_len = int(rule.get("max_len") or 0)
    text = "" if value is None else str(value)
    ok = len(text) <= max_len
    return {"name": "upload_spec", "passed": ok, "column": group,
            "max_len": max_len, "rule_id": rule.get("id"),
            "tiktok_error": rule.get("tiktok_error"),
            "detail": "%s len=%d max=%d (%s)" % (group, len(text), max_len,
                                                 "OK" if ok else rule.get("tiktok_error", "超限"))}


def supported_markets():
    """本地化配置里支持的市场代码列表(ph/th/vn)。"""
    return list((_cfg()["localization"].get("markets") or {}).keys())
