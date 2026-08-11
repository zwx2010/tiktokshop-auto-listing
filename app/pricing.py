"""定价模块 —— 从 market_pricing.json 参数反推展示价/目标价。

设计（面试讲点）：
- 定价是确定性计算，参数全部走配置文件，公式用纯函数实现、可单测；
- 不做 AI 定价 —— AI 会飘、不可复现，价格逻辑必须是确定的。
"""
import math

from .config import get_market_pricing


def _hidden_shipping(profile: dict, weight_g: int) -> float:
    """按重量分段藏价。10g 档起步，每 +10g 加一档。"""
    base = float(profile.get("hidden_shipping_base", 1))
    step = float(profile.get("hidden_shipping_step", 1))
    tiers = max(0, math.ceil(weight_g / 10) - 1)
    return round(base + tiers * step, 2)


def price_skus(market: str, cost_cny: float, weight_g: int) -> dict:
    """给定市场/成本/重量，返回定价结果。"""
    cfg = get_market_pricing()
    p = cfg["markets"][market]
    discount = float(cfg["discount_rate"])
    margin = float(cfg["target_margin"])
    extra = float(cfg["extra_buffer_rate"])
    commission = float(p["commission_rate"])
    payment = float(p["payment_rate"])
    tax = float(p["tax_rate"])
    affiliate = float(p["affiliate_rate"])
    exchange = float(p["exchange_rate"])
    buyer_shipping = float(p["buyer_shipping"])
    infra = float(p["infrastructure_fee"])
    packaging = float(p["packaging_cny"])
    round_inc = float(p.get("round_increment", 1))

    hidden = _hidden_shipping(p, weight_g)
    cost_php = cost_cny / exchange
    cost_pack_php = (cost_cny + packaging) / exchange

    def ceil_inc(v: float) -> float:
        return math.ceil(v / round_inc) * round_inc

    candidate_a = (
        (cost_php + hidden + infra + buyer_shipping * (margin + payment + extra))
        / (1 - margin - commission - payment - tax - affiliate - extra)
    )
    candidate_b = (
        (cost_pack_php + hidden + infra + buyer_shipping * (margin + payment))
        / (1 - margin - commission - payment - tax - affiliate)
    )
    target = ceil_inc(max(candidate_a, candidate_b))
    display = ceil_inc(target / (1 - discount))
    return {
        "currency": p["currency"],
        "cost_cny": cost_cny,
        "hidden_shipping": hidden,
        "target_sale_price": target,
        "display_price": display,
        "discount_rate": discount,
        "formula": "max(A,B) then divide by (1-discount)",
    }
