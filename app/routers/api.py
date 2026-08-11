"""API 路由 —— 影刀 RPA 的对接入口（HTTP 契约）。"""
import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..coze_client import get_coze_client
from ..database import get_db
from ..excel_export import workbook_bytes
from ..models import Account, Listing, Product, ProductSku, Task
from ..pipeline import ingest_capture

router = APIRouter()

# 影刀对长字符串有截断问题（请求体/响应都会截），所以把原始 body 落盘，
# 排查时直接读文件，不依赖影刀侧回显。
_BODY_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "ingest_body.log"
)
_REJECT_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "ingest_rejected.log"
)


def _log_ingest_body(raw: bytes, note: str = ""):
    os.makedirs(os.path.dirname(_BODY_LOG), exist_ok=True)
    with open(_BODY_LOG, "ab") as f:
        f.write(b"===== %s len=%d note=%s =====\n" % (
            datetime.now().isoformat().encode("utf-8"), len(raw), note.encode("utf-8")))
        f.write(raw)
        f.write(b"\n")


def _log_rejected(goods_id: str, reason: str):
    """被拒商品落盘（采集有失败监控，不是黑盒）。

    每一行 = 一条拒收：时间 | 商品ID | 原因。跑完直接打开
    data/ingest_rejected.log 就能看到拒了几个、每个什么原因。
    """
    os.makedirs(os.path.dirname(_REJECT_LOG), exist_ok=True)
    with open(_REJECT_LOG, "a", encoding="utf-8") as f:
        f.write("%s\t%s\t%s\n" % (datetime.now().isoformat(), goods_id or "-", reason))


def _tol_loads(raw: bytes):
    """影刀 body 的容错解析（不挑上游格式）。

    实测影刀会：
      1. 把捕获拼成 {"capture": {…}} 但丢掉外层右括号和 market/account_name 后缀；
      2. 偶尔把整个 body 再包一层 JSON 字符串。
    这里逐级补一个右括号尝试，都失败就把最后一次异常抛出去走 400 诊断。
    """
    last_err = None
    for c in (raw, raw + b"}"):
        try:
            return json.loads(c.decode("utf-8"))
        except json.JSONDecodeError as e:
            last_err = e
    raise last_err


def _get_or_create_account(db: Session, name: str, market: str) -> Account:
    acc = db.query(Account).filter(Account.name == name).first()
    if not acc:
        acc = Account(name=name, market_code=market, store_name="RoseSeek", status="active")
        db.add(acc)
        db.flush()
    return acc


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {
        "accounts": db.query(func.count(Account.id)).scalar(),
        "products": db.query(func.count(Product.id)).scalar(),
        "skus": db.query(func.count(ProductSku.id)).scalar(),
        "listings": db.query(func.count(Listing.id)).scalar(),
        "tasks": db.query(func.count(Task.id)).scalar(),
    }


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)):
    rows = db.query(Account).order_by(Account.id).all()
    return [
        {"id": a.id, "name": a.name, "market": a.market_code, "store": a.store_name,
         "status": a.status, "api_status": a.api_status, "health": a.health_score}
        for a in rows
    ]


@router.get("/products")
def products(db: Session = Depends(get_db), limit: int = 100):
    rows = db.query(Product).order_by(Product.id.desc()).limit(limit).all()
    out = []
    for p in rows:
        out.append({
            "id": p.id,
            "goods_id": p.source_goods_id,
            "source": p.source_platform,
            "title_cn": p.title_cn,
            "category": p.category,
            "color_count": len({s.color for s in p.skus}),
            "style_count": len({s.style for s in p.skus if s.style}),
            "sku_count": len(p.skus),
            "cost_cny": p.cost_cny_used,
            "cost_source": p.cost_source,
            "images": len(p.image_urls or []),
            "status": p.status,
            "created_at": p.created_at,
        })
    return out


@router.get("/listings")
def listings(db: Session = Depends(get_db), limit: int = 100):
    rows = db.query(Listing).order_by(Listing.id.desc()).limit(limit).all()
    return [
        {
            "id": l.id,
            "product_id": l.product_id,
            "account_id": l.account_id,
            "market": l.market_code,
            "title": l.title,
            "price": l.price,
            "target_sale_price": l.target_sale_price,
            "currency": l.currency,
            "seller_sku": l.seller_sku,
            "status": l.listing_status,
            "sku_count": len(l.sku_snapshot or []),
        }
        for l in rows
    ]


@router.get("/products/export")
def export_products(db: Session = Depends(get_db)):
    """看板「导出Excel」：返回全部商品/SKU 的 xlsx 下载流。"""
    from datetime import datetime

    data = workbook_bytes(db)
    filename = f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tasks")
def tasks(db: Session = Depends(get_db), limit: int = 50):
    rows = db.query(Task).order_by(Task.id.desc()).limit(limit).all()
    return [
        {"id": t.id, "type": t.task_type, "account_id": t.account_id,
         "status": t.status, "attempts": t.attempts, "max_attempts": t.max_attempts,
         "error": t.error_message, "created_at": t.created_at}
        for t in rows
    ]


@router.post("/ingest")
async def ingest(request: Request, db: Session = Depends(get_db)):
    """影刀 RPA 调这里：POST 一个采集 JSON（capture 结构），返回商品与上架记录。

    容错设计：影刀可能把整个请求体再包一层 JSON 字符串
    （"{\\"capture\\":...}"），这里统一解掉一层再入库 —— 不挑上游格式。
    """
    raw = await request.body()
    _log_ingest_body(raw, "ingest")
    try:
        data = _tol_loads(raw)
    except json.JSONDecodeError as e:
        # 原始 body 已落盘 data/ingest_body.log，直接读文件看内容；
        # 这里只回短响应（影刀会截断长响应）。
        _log_rejected("-", f"body not valid json: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "detail": f"body not valid json: {e}",
                "received_len": len(raw),
                "saved_to": "data/ingest_body.log",
            },
        )
    if isinstance(data, str):
        # 影刀把整个 body 包成字符串（"{\"capture\":...}"），解开再容忍一次
        try:
            data = _tol_loads(data.encode("utf-8"))
        except json.JSONDecodeError as e:
            _log_rejected("-", f"string-wrapped body not valid json: {e}")
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "detail": f"string-wrapped body not valid json: {e}",
                    "received_len": len(raw),
                    "saved_to": "data/ingest_body.log",
                },
            )
    capture = data.get("capture")
    if isinstance(capture, str):
        capture = json.loads(capture)
    market = str(data.get("market") or "TH").upper()
    account_name = str(data.get("account_name") or "")

    acc = _get_or_create_account(db, account_name or f"RoseSeek_{market}", market)
    try:
        product = ingest_capture(db, capture, market=market, account_id=acc.id, coze=get_coze_client())
    except ValueError as e:
        # 降级页/脏数据拒收：清掉本次事务的未提交改动，回 400（影刀循环继续下一件，不中断）
        db.rollback()
        _log_rejected(str(capture.get("goods_id") or ""), str(e))
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "detail": str(e),
                "goods_id": capture.get("goods_id"),
                "saved_to": "data/ingest_body.log",
            },
        )
    return {
        "ok": True,
        "product_id": product.id,
        "goods_id": product.source_goods_id,
        "sku_count": len(product.skus),
        "status": product.status,
    }
