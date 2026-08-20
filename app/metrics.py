"""Shared operational metrics queries."""

from sqlalchemy import func

from .models import Product


def active_product_count(db) -> int:
    """Return the count used by both the dashboard and operational API."""
    return db.query(func.count(Product.id)).filter(Product.active == True).scalar() or 0
