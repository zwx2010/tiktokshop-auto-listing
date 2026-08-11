"""数据模型 —— 数据库 Schema 文档落地（Phase 1 核心表）。

设计要点（面试讲点）：
- 每张表回答一个业务问题；关键写入有唯一约束保证幂等；
- account_id 贯穿，支撑多账号隔离；
- 状态用状态机枚举，不用散落布尔值。
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# SQLite 只认 INTEGER 自增主键；MySQL 用 BIGINT。用 with_variant 做到平台无关。
BigIntPk = BigInteger().with_variant(Integer, "sqlite")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    platform: Mapped[str] = mapped_column(String(32), default="tiktok_shop")
    market_code: Mapped[str] = mapped_column(String(2), default="TH")
    store_name: Mapped[str] = mapped_column(String(100), default="RoseSeek")
    shop_id: Mapped[str] = mapped_column(String(64), default="")
    api_status: Mapped[str] = mapped_column(String(16), default="none")
    rpa_profile_dir: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="inactive")  # inactive/logging_in/active/limited/disabled
    health_score: Mapped[float] = mapped_column(default=100.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    market_code: Mapped[str] = mapped_column(String(2), default="TH")
    product_line: Mapped[str] = mapped_column(String(32), default="accessories")
    keyword: Mapped[str] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(Integer, default=1)
    group_tag: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    source_platform: Mapped[str] = mapped_column(String(16), default="pdd")  # pdd / 1688
    source_goods_id: Mapped[str] = mapped_column(String(64))
    market_code: Mapped[str] = mapped_column(String(2), default="TH")
    product_line: Mapped[str] = mapped_column(String(32), default="accessories")
    title_cn: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    main_image_url: Mapped[str] = mapped_column(String(1000), default="")
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    cost_cny_used: Mapped[float] = mapped_column(default=0.0)
    cost_source: Mapped[str] = mapped_column(String(32), default="")  # sku_matrix/detail_price/search_buffered/fallback
    weight_g: Mapped[int] = mapped_column(Integer, default=80)
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True)  # pdd:{goods_id}
    status: Mapped[str] = mapped_column(String(16), default="ingested")  # discovered/ingested/listed/failed
    skip_reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    skus: Mapped[list["ProductSku"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductSku(Base):
    __tablename__ = "product_skus"
    __table_args__ = (UniqueConstraint("product_id", "color", "style", name="uq_product_color_style"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"))
    color: Mapped[str] = mapped_column(String(64), default="Default")
    style: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[str] = mapped_column(String(32), default="One Size")
    supplier_sku_id: Mapped[str] = mapped_column(String(64), default="")
    cost_cny: Mapped[float] = mapped_column(default=0.0)
    stock: Mapped[int] = mapped_column(Integer, default=500)

    product: Mapped["Product"] = relationship(back_populates="skus")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("product_id", "account_id", "market_code", name="uq_product_account_market"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    market_code: Mapped[str] = mapped_column(String(2), default="TH")
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float] = mapped_column(default=0.0)  # 展示价
    target_sale_price: Mapped[float] = mapped_column(default=0.0)  # 目标成交价
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    discount_rate: Mapped[float] = mapped_column(default=0.25)
    seller_sku: Mapped[str] = mapped_column(String(128), unique=True)
    listing_status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/ready/submitted/failed
    sku_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(32), default="ingest")  # collect/ingest/copy/upload/sync_orders/sync_messages
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/success/failed/waiting_human
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
