"""数据管线 —— 采集 JSON → 清洗 → 定价 → 商品/SKU → 上架记录。


- ingest_capture() 幂等：dedup_key 唯一约束，重复灌入不产生脏数据；
- 变体提取 + 去重（同色同款保留更高价），延续现工作流的经验；
- build_listing() 把 Coze 文案 + 确定性定价 + 站点维度组装成一条上架记录。
"""
import re

from sqlalchemy.orm import Session

from . import cleaning
from .config import DEFAULT_STORE_NAME
from .application.copy_recovery import record_copy_result
from .coze_client import get_coze_client
from .models import Listing, Product, ProductSku
from .pricing import price_skus

MAX_SKUS_PER_PRODUCT = 80


def _variant_spec(variant: dict, pattern: str) -> str:
    for spec in variant.get("specs") or []:
        if re.search(pattern, str(spec.get("key") or "")):
            return str(spec.get("value") or "")
    return ""


def _build_skus(capture: dict, product_line: str) -> list[dict]:
    """从 SKU 变体提取 颜色+款式，去重（同键保留更高价）。"""
    rows: dict[tuple, dict] = {}
    for variant in capture.get("sku_variants") or []:
        price = cleaning.clean_number(variant.get("group_price_cny") or variant.get("price_cny"))
        if price <= 0:
            continue
        color_raw = _variant_spec(variant, "颜色|color|colour")
        style_raw = _variant_spec(variant, "款式|型号|样式|风格|style|model") if product_line == "accessories" else ""
        color = cleaning.clean_color(color_raw)
        style = cleaning.clean_style(style_raw)
        key = (color, style)
        if key in rows:
            if price > rows[key]["cost_cny"]:  # 保留更高价，避免 TikTok 重复销售属性
                rows[key]["cost_cny"] = price
                rows[key]["supplier_sku_id"] = str(variant.get("sku_id") or "")
        else:
            rows[key] = {
                "color": color,
                "style": style,
                "supplier_sku_id": str(variant.get("sku_id") or ""),
                "cost_cny": price,
            }
    return list(rows.values())[:MAX_SKUS_PER_PRODUCT]


def ingest_capture(
    db: Session,
    capture: dict,
    *,
    market: str = "TH",
    account_id: int,
    store_name: str = DEFAULT_STORE_NAME,
    product_line: str = "accessories",
    coze=None,
) -> Product:
    source_platform = str(capture.get("source_platform") or "pdd")
    goods_id = str(capture.get("goods_id") or "")
    if not goods_id:
        raise ValueError("capture missing goods_id")

    dedup_key = f"{source_platform}:{goods_id}"
    existing = db.query(Product).filter(Product.dedup_key == dedup_key).first()
    if existing:
        # 商品已存在：仍要确保本账号/本站点有上架记录（多账号多站点）
        build_listing(
            db, existing, market=market, account_id=account_id,
            store_name=store_name, coze=coze,
        )
        db.commit()
        return existing

    title_cn = str(capture.get("title") or "")
    category = cleaning.infer_category(title_cn)
    images = cleaning.clean_images(capture.get("images") or [])
    cost_decision = cleaning.select_cost(capture)
    skus = _build_skus(capture, product_line)

    # 降级页拦截（2026-08-09）：PDD 对影刀浏览器返回简化页时，标题空 / 没有可用 SKU / 采集端标记 degraded。
    # 这类数据入库只会污染选品和出表（出表没有标题、颜色款式全是 Default），直接拒收；
    # 影刀循环收到 400 会跳到下一件，不中断整个采集。
    if not title_cn.strip():
        raise ValueError("capture 标题为空，疑似 PDD 降级页（页面未完整渲染/反爬裁剪），已拒绝入库")
    if not skus:
        raise ValueError("capture 没有可用的 SKU 价格，疑似 PDD 降级页，已拒绝入库")
    if capture.get("degraded"):
        raise ValueError("capture 被采集端标记 degraded（无标题或无变体颜色信息），疑似 PDD 降级页，已拒绝入库")

    product = Product(
        source_platform=source_platform,
        source_goods_id=goods_id,
        market_code=market,
        product_line=product_line,
        title_cn=title_cn,
        category=category,
        main_image_url=images[0] if images else "",
        image_urls=images,
        cost_cny_used=cost_decision["cost_cny"],
        cost_source=cost_decision["source"],
        weight_g=int(capture.get("weight_g") or 80),
        dedup_key=dedup_key,
        status="ingested",
    )
    for s in skus:
        product.skus.append(
            ProductSku(
                color=s["color"],
                style=s["style"],
                size="One Size",
                supplier_sku_id=s["supplier_sku_id"],
                cost_cny=s["cost_cny"],
            )
        )
    db.add(product)
    db.flush()
    # 采集时自动赋予管理编号 SPU(唯一),飞书选品/上架表都拿它关联商品
    product.spu = f"SPU{product.id:06d}"

    build_listing(
        db,
        product,
        market=market,
        account_id=account_id,
        store_name=store_name,
        coze=coze,
    )
    db.commit()
    db.refresh(product)
    return product


def build_listing(
    db: Session,
    product: Product,
    *,
    market: str,
    account_id: int,
    store_name: str = DEFAULT_STORE_NAME,
    coze=None,
) -> Listing:
    existing = (
        db.query(Listing)
        .filter(
            Listing.product_id == product.id,
            Listing.account_id == account_id,
            Listing.market_code == market,
        )
        .first()
    )
    if existing:
        return existing

    skus = product.skus
    costs = [s.cost_cny for s in skus if s.cost_cny > 0] or [product.cost_cny_used]
    representative_cost = min(costs)
    pricing = price_skus(market, representative_cost, product.weight_g)

    style_code = str(product.source_goods_id)[-4:]
    ctx = {
        "goods_id": product.source_goods_id,
        "store_name": store_name,
        "style_code": style_code,
        "category": product.category,
        "colors": [s.color for s in skus if s.color and s.color != "Default"],
        "styles": [s.style for s in skus if s.style],
        "source_title_cn": product.title_cn,
    }
    # 真实文案源(codex 表 / Coze HTTP)取不到时,不抛错中断入库:
    # get_coze_client() 无 codex 数据/COZE_API_TOKEN 也会抛 —— 一起包进 try;
    # 创建 Listing 但标 copy_missing —— export 会跳过缺文案的件,绝不带假/空文案上架;
    # 之后由 scripts/regenerate_listing_copy.py --mode llm 用真实 Claude 补文案再标 ready。
    try:
        coze = coze or get_coze_client()
        copy = coze.generate_copy(ctx, market)
        title, desc = copy.title, copy.description
        copy_source = getattr(copy, "source", "")
        copy_reason = "" if (title and desc) else "provider returned empty title or description"
    except Exception as exc:
        print(f"[build_listing] 文案源不可用({product.source_goods_id}/{market}): "
              f"{str(exc)[:80]} → copy_missing", flush=True)
        title, desc = "", ""
        copy_source = ""
        copy_reason = str(exc)[:500]

    copy_result = {
        "title": title,
        "description": desc,
        "source": copy_source,
        "valid": bool(title and desc),
        "reason": copy_reason,
    }

    # SKU 快照：逐 SKU 定价
    snapshot = []
    for i, s in enumerate(skus):
        p = price_skus(market, s.cost_cny, product.weight_g)
        snapshot.append(
            {
                "seller_sku": f"PDD-{market}-{product.source_goods_id}-{i + 1:03d}",
                "color": s.color,
                "style": s.style,
                "price": p["display_price"],
                "target_sale_price": p["target_sale_price"],
                "stock": s.stock,
            }
        )

    listing = Listing(
        product_id=product.id,
        account_id=account_id,
        market_code=market,
        title=title,
        description=desc,
        price=pricing["display_price"],
        target_sale_price=pricing["target_sale_price"],
        currency=pricing["currency"],
        discount_rate=pricing["discount_rate"],
        seller_sku=snapshot[0]["seller_sku"] if snapshot else f"PDD-{market}-{product.source_goods_id}",
        sku_snapshot=snapshot,
    )
    record_copy_result(listing, **copy_result)
    db.add(listing)
    db.flush()
    return listing
