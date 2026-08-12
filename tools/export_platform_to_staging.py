#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""平台库 → 工作流 staging → 一键出 TikTok 上架表。

从平台库 SQLite 读商品/SKU，按工作流「tk自动化工作流」同款 46 列组装
staging CSV（字段格式逐列对齐 Convert-PddCaptureToStaging.ps1 产物），
再调用工作流 Fill-TikTokTemplate.ps1 填官方配饰模板，一步生成上架 xlsx。

用法：
    python tools/export_platform_to_staging.py --market ph --count 10                 # 自动选品出表
    python tools/export_platform_to_staging.py --market ph --count 10 --skip-fill    # 只出 staging 先核对
    python tools/export_platform_to_staging.py --selected-file data/selected.xlsx    # 人工选品表出表

设计要点：
- 数据源单一（平台库 DB），采集入库的商品随时能一键出表；
- 定价复用 app/pricing.price_skus（确定性公式，与工作流逐位一致），不依赖 sku_snapshot；
- 两条出表路径：自动选品（成本窗口 [2,40] + 品牌词过滤 + 标题去重）或人工选品
  （--selected-file 传 Excel，按表里商品出、SKU 全出，人工已把关不再过滤）；
- 文案只走真实来源：codex_listing_copy.csv 回填地道文案，其余用平台库真实 Listing 文案
  （platform:listing）；codex/listing 都缺 → platform:missing，跳过出表，不带空/假文案上架。
"""
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import cleaning  # noqa: E402
from app.config import CONFIG_DIR, load_json  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Listing, Product, ProductSku  # noqa: E402
from app.pricing import price_skus  # noqa: E402

# 46 列与工作流 runs/smoke_01/ph/tiktok_ph_listing_staging.csv 表头完全一致
COLUMNS = [
    "status", "category", "product_title", "product_description", "market_code",
    "listing_language", "sku", "supplier_sku_id", "color", "size", "style",
    "raw_color", "raw_size", "raw_style", "material", "price_php", "price_local",
    "currency", "cost_cny_used", "cost_source", "search_price_cny",
    "detail_price_cny", "sku_price_count", "known_price_min_cny",
    "known_price_max_cny", "target_sale_price_php", "target_sale_price_local",
    "discount_rate", "ph_hidden_shipping_php", "hidden_shipping_local",
    "pricing_formula", "stock", "weight_g", "package_length_cm",
    "package_width_cm", "package_height_cm", "image_urls", "sku_image_url",
    "size_chart", "size_chart_source", "size_chart_local_path", "size_chart_key",
    "supplier_url", "keywords", "listing_copy_source", "risk_notes",
]

BRAND_WORDS = [
    "apm", "caponi", "fanci", "daisy dream", "loveixcc", "吴越老银铺",
]

# TikTok 模板规格:销售属性值名称(style/color 映射的 property_value)不得超过 50 字符。
# run 8ef06c39 真失败:codex styles_en 里 51/64 字符的值被 TikTok 解析器拒收。
# 超出的一律整件跳过(不截断),与 RAG 的 upload_learning 规则(见 app/rag)一致。
MAX_STYLE_LEN = 50

LANGUAGES = {"PH": "English", "TH": "Thai", "VN": "Vietnamese"}

PRICING_FORMULA = (
    "{MKT} table from 原表5: display=round_up(target_sale/(1-discount)); "
    "target_sale=round_up(max(buffer,no_buffer))"
)
RISK_NOTES = (
    "Platform library export: verify image authorization, exact material, "
    "actual stock, package weight, and PDD supplier stability."
)

# 上架列默认值（产品线级、显式可调）读 config/listing_defaults.json；
# 缺文件时回退到与工作流一致的默认值。
_DEF = load_json(CONFIG_DIR / "listing_defaults.json") if (CONFIG_DIR / "listing_defaults.json").is_file() else {}
DEFAULT_WEIGHT_G = float(_DEF.get("default_weight_g", 80))
DEFAULT_STOCK = int(_DEF.get("default_stock", 500))
PACKAGE_DIMS = tuple(_DEF.get("package_dims", [28, 22, 4]))
MATERIAL = str(_DEF.get("material", "Polyester blend"))
FASHION_ROOT = "Fashion Accessories"
# 出表时过滤掉的商品图最小边长(px)。对应工作流 FINAL_UPLOAD_TEMPLATE_RULES.md 的
# 「低于 300×300 上传报错」阈值;过滤后有效大图才能填满更多格(最多 9 格)。
GALLERY_MIN_DIM = int(_DEF.get("gallery_image_min_dim", 300))


def load_products(db) -> list[Product]:
    """按采集顺序（id 升序）加载全部商品。"""
    return db.query(Product).order_by(Product.id).all()


def load_skus(db, product_ids) -> dict[int, list[ProductSku]]:
    rows = (
        db.query(ProductSku)
        .filter(ProductSku.product_id.in_(product_ids))
        .order_by(ProductSku.product_id, ProductSku.id)
        .all()
    )
    by_pid: dict[int, list[ProductSku]] = {}
    for s in rows:
        by_pid.setdefault(s.product_id, []).append(s)
    return by_pid


def load_listings(db, product_ids) -> dict[int, dict[str, Listing]]:
    """每商品的市场 Listing 映射（PH 优先，TH 兜底 Listing 文案）。"""
    rows = (
        db.query(Listing)
        .filter(Listing.product_id.in_(product_ids))
        .order_by(Listing.product_id, Listing.market_code)
        .all()
    )
    by_pid: dict[int, dict[str, Listing]] = {}
    for l in rows:
        by_pid.setdefault(l.product_id, {})[l.market_code] = l
    return by_pid


def load_selected_gids(path: Path) -> list[str]:
    """读人工选品表（xlsx），返回商品ID列表（按 Excel 行序去重）。

    表头行找含 goods_id 或 商品ID 的列；值需纯数字 ≥8 位（防御混入其他列）。
    """
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        sys.exit(f"无法读取选品表 {path}: {e}")
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if not header:
        sys.exit(f"选品表没有表头行: {path}")
    col = None
    for i, h in enumerate(header):
        label = str(h or "").lower()
        if "goods_id" in label or "商品id" in label:
            col = i
            break
    if col is None:
        sys.exit(f"选品表找不到商品ID列（表头需含 goods_id 或 商品ID）: {path}")
    gids: list[str] = []
    seen: set[str] = set()
    for row in it:
        v = str(row[col]).strip() if col < len(row) and row[col] is not None else ""
        if re.fullmatch(r"\d{8,}", v) and v not in seen:
            seen.add(v)
            gids.append(v)
    if not gids:
        sys.exit(f"选品表没有可识别的商品ID（{col} 列全空或格式不符）: {path}")
    return gids


def load_codex_copy(path: Path) -> dict[str, dict]:
    """读工作流 codex_listing_copy.csv，key=goods_id。缺文件返回空 dict。"""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {str(r["goods_id"]).strip(): r for r in rows if r.get("goods_id")}


def build_style_map(codex_row: dict | None) -> dict[str, str]:
    """codex styles(中文) 与 styles_en(英文) 按位 zip 建查表。查不到原样透传。"""
    if not codex_row:
        return {}
    cn = [s.strip() for s in (codex_row.get("styles") or "").split("|") if s.strip()]
    en = [s.strip() for s in (codex_row.get("styles_en") or "").split("|") if s.strip()]
    return dict(zip(cn, en))


def resolve_style_en(sku, style_map: dict[str, str]) -> str:
    """SKU 上传用英文款式:优先库内 style_en(本地化链路落库),其次 codex 映射,
    都没有才原样透传(此时若为中文,是还没被本地化的漏网款式)。"""
    if not (sku.style or "").strip():
        return ""
    en = (getattr(sku, "style_en", "") or "").strip()
    if en:
        return en
    return style_map.get(sku.style, sku.style)


def select_products(
    products: list[Product], min_cost: float, max_cost: float, count: int
) -> list[Product]:
    """成本窗口 + 品牌词过滤 + 标题去重，id 序抽前 count 件。

    count=0 表示不封顶:返回全部合格候选(配合"没指定数量→出全部"的指令语义)。
    """
    seen_titles: set[str] = set()
    out: list[Product] = []
    for p in products:
        if not (min_cost <= p.cost_cny_used <= max_cost):
            continue
        title_cn = (p.title_cn or "").strip()
        if not title_cn:
            continue
        low = title_cn.lower()
        if any(bw in low for bw in BRAND_WORDS):
            continue
        if title_cn in seen_titles:
            continue
        seen_titles.add(title_cn)
        out.append(p)
        if count and len(out) >= count:
            break
    return out


def resolve_copy(
    gid: str, listing_map: dict, codex_map: dict[str, dict]
) -> tuple[str, str, str, str, dict | None]:
    """返回 (title, description, source_keyword, listing_copy_source, codex_row)。"""
    if gid in codex_map:
        r = codex_map[gid]
        return (
            str(r.get("title") or ""),
            str(r.get("description") or ""),
            str(r.get("source_keyword") or ""),
            "codex:interactive",
            r,
        )
    # 真实平台库 Listing 文案（PH 优先，TH 兜底）；都没有 → platform:missing，调用方跳过
    for mkt in ("PH", "TH"):
        lst = listing_map.get(mkt)
        if lst and (lst.title or lst.description):
            return lst.title, lst.description, "", "platform:listing", None
    return "", "", "", "platform:missing", None


# 图片尺寸过滤用:qwen_vision.py --meta(本机解析宽高,0 成本)定位方式与
# import_1688_captures_to_db.py 同源 —— ROSEEK_PKG_DIR 可覆盖,缺省用项目根相对路径。
_PKG = os.environ.get(
    "ROSEEK_PKG_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "RoseSeek_TikTokShop_AI_Localized_20260809"),
)
QWEN_TOOL = os.path.join(_PKG, "tools", "qwen_vision.py")
# 尺寸缓存(去重下载 + 判定落盘),data/ 已整体 gitignore,不入库
_IMG_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "img_dim_cache"
_DIMS_JSON = Path(__file__).resolve().parent.parent / "data" / "img_dims.json"
_DIMS_CACHE: dict[str, list] | None = None      # url -> [w, h] 或 [0](判不出/失败)
_DIMS_MEMO: dict[str, list] = {}                 # 单次运行内记忆,避免重复下载/解析


def _load_dims_cache():
    global _DIMS_CACHE
    if _DIMS_CACHE is None:
        _DIMS_CACHE = {}
        try:
            if _DIMS_JSON.is_file():
                _DIMS_CACHE.update(json.loads(_DIMS_JSON.read_text(encoding="utf-8")))
        except Exception:
            _DIMS_CACHE = {}
    return _DIMS_CACHE


def _save_dims_cache():
    try:
        _DIMS_JSON.parent.mkdir(parents=True, exist_ok=True)
        _DIMS_JSON.write_text(json.dumps(_DIMS_CACHE, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _download(url: str, outpath: Path) -> bool:
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < 50:
                raise ValueError("body too small")
            outpath.parent.mkdir(parents=True, exist_ok=True)
            outpath.write_bytes(data)
            return True
        except Exception:
            if _ >= 1:
                return False
            time.sleep(1.5)
    return False


def _parse_dims(url: str):
    """返回 [w, h] 或 None。下载失败/无法解析 → None(该图判不过,过滤掉)。"""
    if url in _DIMS_MEMO:
        dims = _DIMS_MEMO[url]
        return (dims[0], dims[1]) if dims and dims[0] else None
    dims = _load_dims_cache().get(url)
    if dims is not None:
        _DIMS_MEMO[url] = dims
        return (dims[0], dims[1]) if dims[0] else None
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".img"
    local = _IMG_CACHE_DIR / (hashlib.md5(url.encode("utf-8")).hexdigest() + ext)
    if not local.is_file() and not _download(url, local):
        _DIMS_MEMO[url] = [0]
        return None
    try:
        r = subprocess.run([sys.executable, QWEN_TOOL, str(local), "--meta"],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=60)
        w = h = 0
        for line in (r.stdout or "").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if os.path.abspath(str(d.get("path") or "")) == os.path.abspath(str(local)):
                w, h = d.get("width") or 0, d.get("height") or 0
                break
        if not w or not h:
            raise ValueError("无法解析宽高")
    except Exception:
        w = h = 0
    if not w or not h:
        _DIMS_MEMO[url] = [0]
        _load_dims_cache()[url] = [0]
        _save_dims_cache()
        return None
    _DIMS_MEMO[url] = [w, h]
    _load_dims_cache()[url] = [w, h]
    _save_dims_cache()
    return w, h


def filter_small_images(urls, min_dim: int) -> list[str]:
    """过滤小尺寸/判不过的图片,只留边长 ≥ min_dim 的大图,保持原序去重。

    下载或解析失败的 URL 也算不过(上传前审图同样会 FAIL),一并滤掉。
    全部被滤空时兜底保留第一张(商品必须有主图,宁留小图也不让主图空缺;
    审图闸门仍会按 TikTok 下限单独判它)。不造假 —— 保留的都是真实存在的图。
    """
    seen: list[str] = []
    for raw in (urls or []):
        url = str(raw).strip()
        if not url or url in seen:
            continue
        dims = _parse_dims(url)
        if dims and dims[0] >= min_dim and dims[1] >= min_dim:
            seen.append(url)
    if not seen and urls:
        seen.append(str(urls[0]).strip())
    return seen[:9]


def make_row(
    p: Product,
    sku: ProductSku,
    title: str,
    description: str,
    src_kw: str,
    copy_source: str,
    style_map: dict[str, str],
    mkt: str,
    sku_costs: list[float],
) -> dict:
    """组装 46 列的一行。定价每 SKU 用 price_skus 重算。"""
    pricing = price_skus(mkt, sku.cost_cny, p.weight_g or DEFAULT_WEIGHT_G)
    # style 透传不截断；超 MAX_STYLE_LEN 的 SKU 由调用方在 append 前整件跳过（见 main）
    style_en = resolve_style_en(sku, style_map)
    category = p.category or cleaning.infer_category(p.title_cn)
    images = filter_small_images(cleaning.clean_images(p.image_urls), GALLERY_MIN_DIM)
    low = min(sku_costs) if sku_costs else 0.0
    high = max(sku_costs) if sku_costs else 0.0

    return {
        "status": "Review",
        "category": f"{FASHION_ROOT} > {category}",
        "product_title": title,
        "product_description": description,
        "market_code": mkt,
        "listing_language": LANGUAGES.get(mkt, "English"),
        "sku": re.sub(
            r"[^A-Z0-9-]", "",
            f"PDD-{mkt}-{p.source_goods_id}-{sku.supplier_sku_id}".upper(),
        ),
        "supplier_sku_id": sku.supplier_sku_id,
        "color": cleaning.clean_variant_value(sku.color),
        "size": sku.size,
        "style": style_en,
        "raw_color": sku.color,
        "raw_size": sku.size,
        "raw_style": "",
        "material": MATERIAL,
        "price_php": pricing["display_price"],
        "price_local": pricing["display_price"],
        "currency": pricing["currency"],
        "cost_cny_used": sku.cost_cny,
        "cost_source": p.cost_source or "platform",
        "search_price_cny": low,
        "detail_price_cny": low,
        "sku_price_count": len(sku_costs),
        "known_price_min_cny": low,
        "known_price_max_cny": high,
        "target_sale_price_php": pricing["target_sale_price"],
        "target_sale_price_local": pricing["target_sale_price"],
        "discount_rate": pricing["discount_rate"],
        "ph_hidden_shipping_php": pricing["hidden_shipping"],
        "hidden_shipping_local": pricing["hidden_shipping"],
        "pricing_formula": PRICING_FORMULA.format(MKT=mkt),
        "stock": sku.stock or DEFAULT_STOCK,
        "weight_g": p.weight_g or DEFAULT_WEIGHT_G,
        "package_length_cm": PACKAGE_DIMS[0],
        "package_width_cm": PACKAGE_DIMS[1],
        "package_height_cm": PACKAGE_DIMS[2],
        "image_urls": "|".join(images),
        "sku_image_url": "",
        "size_chart": "",
        "size_chart_source": "not_required_accessories",
        "size_chart_local_path": "",
        "size_chart_key": "",
        "supplier_url": f"https://mobile.pinduoduo.com/goods.html?goods_id={p.source_goods_id}",
        "keywords": (
            f"{src_kw}|fashion accessories|daily outfit|casual style|philippines fashion"
        ),
        "listing_copy_source": copy_source,
        "risk_notes": RISK_NOTES,
    }


def write_staging(rows: list[dict], path: Path) -> None:
    """utf-8-sig 写 CSV（PS5.1 Import-Csv 无 BOM 读中文会乱码）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def run_fill(fill_ps1: Path, template_path: Path, csv_path: Path, out_path: Path,
             english: bool = False, max_images: int = 5) -> None:
    # 强制 powershell 以 UTF-8 输出,并让 subprocess 用 UTF-8 容错解码。
    # 否则在部分环境(如 claude -p 子进程)下 text=True 走 GBK 解码,
    # 遇到非法字节 reader 线程直接崩,result.stdout 变 None。
    # max_images:商品图填几张(E..M)。导出路径图已做尺寸/有效过滤,可填满 9 格;
    # ps1 缺省 5 张(其他管线的小图安全线),这里显式传 9。
    lang_switch = " -English" if english else ""
    img_switch = f" -MaxImages {int(max_images)}"
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-Command",
        "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "& '{0}' -TemplatePath '{1}' -StagingCsvPath '{2}' -OutputPath '{3}'{4}{5}".format(
            fill_ps1, template_path, csv_path, out_path, lang_switch, img_switch
        ),
    ]
    print("调用 PowerShell 填表...")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        sys.exit(f"Fill-TikTokTemplate.ps1 失败:\n{result.stdout}\n{result.stderr}")
    print(result.stdout.strip())


def resolve_workflow_dir(args) -> Path:
    if args.workflow_dir:
        return Path(args.workflow_dir)
    env = os.environ.get("TIKTOK_WORKFLOW_DIR")
    if env:
        return Path(env)
    base = Path(__file__).resolve().parent.parent
    return base.parent / "tk自动化工作流"


def main() -> None:
    ap = argparse.ArgumentParser(description="平台库 → 工作流 staging → TikTok 上架表")
    ap.add_argument("--market", default="ph", choices=["ph", "th", "vn"],
                    help="目标市场（默认 ph）")
    ap.add_argument("--count", type=int, default=10, help="每张表抽前 N 件商品")
    ap.add_argument("--min-cost", type=float, default=2, help="成本窗口下界（商品/SKU 共用）")
    ap.add_argument("--max-cost", type=float, default=40, help="成本窗口上界")
    ap.add_argument("--workflow-dir", default=None, help="工作流根目录（默认自动推导）")
    ap.add_argument("--run-dir", default=None, help="输出目录（默认 {工作流}/runs/platform_export_时间戳/{market}）")
    ap.add_argument("--template", default=None, help="模板文件名（默认 accessories_{market}.xlsx）")
    ap.add_argument("--skip-fill", action="store_true", help="只出 staging CSV，不出 xlsx")
    ap.add_argument("--no-sku-cost-filter", action="store_true",
                    help="关闭 SKU 级成本过滤（默认开，与工作流 staging 行数对齐）")
    ap.add_argument("--selected-file", default=None,
                    help="人工选品表(xlsx)：按表里商品出表（人工把关，跳过品牌词/成本窗口，SKU 全出）")
    args = ap.parse_args()

    mkt = args.market.upper()
    wf = resolve_workflow_dir(args)
    # 英文模板优先:有 accessories_{market}_en.xlsx 就用英文版(上传表彻底无汉字);
    # 没有就回退中文模板。显式 --template 时不自动切换。
    if args.template:
        template_path = wf / "templates" / args.template
        english = "_en." in args.template.lower()
    else:
        base = f"accessories_{args.market.lower()}"
        en_path = wf / "templates" / f"{base}_en.xlsx"
        template_path = en_path if en_path.exists() else wf / "templates" / f"{base}.xlsx"
        english = en_path.exists()
    fill_ps1 = wf / "tools" / "Fill-TikTokTemplate.ps1"
    codex_path = wf / "runs" / "smoke_01" / args.market.lower() / "codex_listing_copy.csv"

    if not template_path.exists():
        sys.exit(f"模板不存在: {template_path}")
    if not fill_ps1.exists():
        sys.exit(f"Fill-TikTokTemplate.ps1 不存在: {fill_ps1}")

    selected_mode = bool(args.selected_file)
    db = SessionLocal()
    try:
        if selected_mode:
            # 人工选品：按 Excel 行序取商品（人工把关，不品牌词/成本窗口过滤）
            gid_list = load_selected_gids(Path(args.selected_file))
            prod_map = {
                p.source_goods_id: p
                for p in db.query(Product).filter(Product.source_goods_id.in_(gid_list))
            }
            missing = [g for g in gid_list if g not in prod_map]
            if missing:
                print(f"[警告] 库中没有的 goods_id（已跳过）: {missing}")
            selected = [prod_map[g] for g in gid_list if g in prod_map]
            if not selected:
                sys.exit("选品表中的商品在库里都不存在")
        else:
            products = load_products(db)
            selected = select_products(products, args.min_cost, args.max_cost, args.count)
            if not selected:
                sys.exit("没有通过选品（成本窗口/品牌词/去重）的商品")
        pids = [p.id for p in selected]
        sku_map = load_skus(db, pids)
        listing_map = load_listings(db, pids)
    finally:
        db.close()

    codex_map = load_codex_copy(codex_path)
    if selected_mode:
        print(f"[人工选品] {len(selected)} 件商品（来自 {args.selected_file}）")
    else:
        print(f"[自动选品] {len(selected)} 件商品（成本 {args.min_cost:.0f}~{args.max_cost:.0f} + 品牌词过滤 + 标题去重）")
    for p in selected:
        print(f"  {p.source_goods_id}  {p.title_cn[:28]}")

    rows = []
    skipped_style = 0
    for p in selected:
        gid = p.source_goods_id
        title, description, src_kw, copy_source, codex_row = resolve_copy(
            gid, listing_map.get(p.id, {}), codex_map
        )
        if copy_source == "platform:missing":
            print(f"[警告] {gid} 无可用真实文案(codex/平台库 Listing 均缺),跳过出表")
            continue
        style_map = build_style_map(codex_row)
        sku_costs = [s.cost_cny for s in sku_map.get(p.id, []) if s.cost_cny > 0]
        for sku in sku_map.get(p.id, []):
            # 人工选品（selected_mode）SKU 全出；自动选品按成本窗口筛
            if (not selected_mode) and (not args.no_sku_cost_filter) and \
                    not (args.min_cost <= sku.cost_cny <= args.max_cost):
                continue
            # 上传规格:style 映射的 property_value 不得超过 50 字符(TikTok 真拒收),
            # 超长整件跳过,不截断 —— 与 RAG upload_learning 规则一致
            style_en = resolve_style_en(sku, style_map)
            if len(style_en) > MAX_STYLE_LEN:
                skipped_style += 1
                print(f"[警告] style 超{MAX_STYLE_LEN}字符已跳过: {sku.supplier_sku_id}  "
                      f"len={len(style_en)}  {style_en[:40]}")
                continue
            rows.append(make_row(p, sku, title, description, src_kw, copy_source,
                                 style_map, mkt, sku_costs))
    if skipped_style:
        print(f"[警告] 共跳过 {skipped_style} 个超长 style 的 SKU(TikTok 属性值限 {MAX_STYLE_LEN} 字符)")

    run_dir = Path(args.run_dir) if args.run_dir else (
        wf / "runs" / f"platform_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}" / args.market.lower()
    )
    csv_path = run_dir / f"tiktok_{args.market.lower()}_listing_staging.csv"
    write_staging(rows, csv_path)
    # 件数按真出行的商品算(超长 style 整件跳过后,SKU 全无的商品不再计入)
    row_products = len({r["product_title"] for r in rows})
    print(f"staging: {csv_path}  （{len(rows)} 行 SKU / {row_products} 件商品）")

    if not args.skip_fill:
        out_path = run_dir / f"{mkt}_upload_top{row_products}.xlsx"
        run_fill(fill_ps1, template_path, csv_path, out_path, english=english,
                 max_images=9)
        print(f"[完成] 上架表格: {out_path}")
        print(f"   含 {row_products} 件商品 / {len(rows)} 行 SKU")


if __name__ == "__main__":
    main()
