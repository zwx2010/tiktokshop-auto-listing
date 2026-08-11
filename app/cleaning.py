"""清洗模块 —— 把拼多多脏数据洗成可上架数据。

面试讲点：确定性清洗（颜色/款式/图片/价格选择）用纯函数实现，
和 AI 能力解耦 —— 能确定的逻辑不交给 AI。
"""
import re

CN_COLOR_MAP = {
    "黑": "Black", "白": "White", "粉": "Pink", "蓝": "Blue", "绿": "Green",
    "红": "Red", "灰": "Gray", "咖": "Brown", "棕": "Brown", "杏": "Apricot",
    "米": "Beige", "紫": "Purple", "黄": "Yellow", "橙": "Orange", "青": "Teal",
    "银": "Silver", "金": "Gold",
}
KNOWN_ENGLISH = [
    "Black", "White", "Pink", "Blue", "Green", "Red", "Gray", "Brown",
    "Apricot", "Beige", "Purple", "Yellow", "Orange", "Teal", "Silver",
    "Gold", "Cream", "Nude",
]
CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
          "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}

MAX_VARIANT_VALUE_LEN = 50  # TikTok 属性值上限 50 字符（实测踩坑后加入）


def clean_color(raw: str) -> str:
    """从任意脏串里抠出真颜色，否则兜底 Default/件数。"""
    text = (raw or "").strip()
    if not text:
        return "Default"
    if re.search("多色|混色|彩色|随机", text):
        return "Multicolor"
    if re.search("肤色|肉色", text):
        return "Nude"

    # 拆开拼接英文：WhiteBlack -> White Black
    split = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    split = re.sub(r"(?i)grey", "gray", split)

    found: list[str] = []
    lower = split.lower()
    for c in KNOWN_ENGLISH:
        if re.search(r"(^|[^a-z])" + re.escape(c.lower()) + r"([^a-z]|$)", lower):
            if c not in found:
                found.append(c)
    for ch in text:
        if ch in CN_COLOR_MAP:
            eng = CN_COLOR_MAP[ch]
            if eng not in found:
                found.append(eng)

    if found:
        return clean_variant_value(" ".join(sorted(set(found))[:3]))

    m = re.search(r"([0-9一二三四五六七八九十]+)\s*件", text)
    if m:
        return clean_variant_value(f"Style {_cn_to_arabic(m.group(1))} Pack")
    if re.search(r"pack", text, re.I):
        clean = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]", "", split)).strip()
        if clean:
            return clean_variant_value(clean)
    return "Default"


def clean_style(raw: str) -> str:
    """款式值清洗：去噪声、去默认占位、限长。"""
    text = (raw or "").strip()
    text = re.sub(r"[\[\]【】（）()\s]+", " ", text).strip(" -_:：")
    if not text:
        return ""
    if re.search(r"均码|默认|Default|one size|One Size", text):
        return ""
    return clean_variant_value(text)


def clean_variant_value(value: str) -> str:
    """TikTok 变体属性值强制 ≤50 字符。"""
    v = (value or "").strip()
    if len(v) > MAX_VARIANT_VALUE_LEN:
        v = v[:MAX_VARIANT_VALUE_LEN].rstrip()
    return v


def clean_images(urls) -> list[str]:
    """过滤促销图/头像/动图/图标，保留商品图，去重。"""
    seen: list[str] = []
    for raw in (urls or []):
        url = str(raw).strip()
        if not url:
            continue
        if re.search(r"share_logo|base/share|avatar|savatar|icon|commimg|promotion|/promo/|/coupon/|ddpay|oms_img|\.gif|slim\.png", url):
            continue
        if not re.search(r"mms-material-img|mms-goods-image|pddpic|pinduoduo|tiktokcdn|open-gw|garner-api-new|alicdn", url):
            continue
        url = re.sub(r"\?.*$", "", url)
        if url not in seen:
            seen.append(url)
    return seen[:9]


def clean_number(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace("￥", ""))
    except (TypeError, ValueError):
        return default


def select_cost(capture: dict) -> dict:
    """成本来源优先级：SKU 矩阵 > 详情价 > 兜底。"""
    variants = capture.get("sku_variants") or []
    sku_costs = [clean_number(v.get("group_price_cny") or v.get("price_cny")) for v in variants]
    sku_costs = [c for c in sku_costs if c > 0]
    if sku_costs:
        return {"cost_cny": min(sku_costs), "source": "sku_matrix", "sku_costs": sku_costs}
    detail = clean_number(capture.get("price_cny"))
    if detail > 0:
        return {"cost_cny": detail, "source": "detail_price", "sku_costs": sku_costs}
    return {"cost_cny": 0.0, "source": "fallback", "sku_costs": sku_costs}


def infer_category(title_cn: str) -> str:
    """从中文标题推断配饰类目（确定性规则）。"""
    t = title_cn or ""
    if re.search(r"发夹|鲨鱼夹|发箍|发绳|发圈|发饰|头饰|发卡", t):
        return "Hair Accessory"
    if re.search(r"耳环|耳钉|耳饰", t):
        return "Earrings"
    if re.search(r"项链|吊坠", t):
        return "Necklace"
    if re.search(r"手链|手镯|脚链", t):
        return "Bracelet"
    if re.search(r"戒指", t):
        return "Ring"
    if re.search(r"腰带|皮带", t):
        return "Belt"
    if re.search(r"丝巾|围巾", t):
        return "Scarf"
    if re.search(r"帽", t):
        return "Hat"
    if re.search(r"墨镜|太阳镜|眼镜", t):
        return "Sunglasses"
    if re.search(r"钥匙扣|包挂", t):
        return "Keychain"
    return "Fashion Accessory"


def _cn_to_arabic(cn: str) -> str:
    return "".join(CN_NUM.get(ch, ch) for ch in cn)
