"""Web 看板页面路由。"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from ..database import SessionLocal
from ..models import Account, Listing, Product, ProductSku
from ..timeutil import fmt_utc

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    db = SessionLocal()
    try:
        stats = {
            "accounts": db.query(func.count(Account.id)).scalar() or 0,
            "products": db.query(func.count(Product.id)).scalar() or 0,
            "skus": db.query(func.count(ProductSku.id)).scalar() or 0,
            "listings": db.query(func.count(Listing.id)).scalar() or 0,
        }
        accounts = db.query(Account).order_by(Account.id).all()
        # selectinload 预加载 skus，避免 Session 关闭后惰性加载报 DetachedInstanceError
        products = (
            db.query(Product)
            .options(selectinload(Product.skus))
            .order_by(Product.id.desc())
            .limit(15)
            .all()
        )
        listings = db.query(Listing).order_by(Listing.id.desc()).limit(15).all()
        # 商品加一列"采集时间"（UTC -> 北京时间），模板直接用
        products_view = [
            {**p.__dict__, "created_at_local": fmt_utc(p.created_at)} for p in products
        ]
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"stats": stats, "accounts": accounts, "products": products_view, "listings": listings},
    )
