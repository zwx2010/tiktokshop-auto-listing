"""数据模型 —— 数据库 Schema 文档落地（Phase 1 核心表）。

设计要点：
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
    Numeric,
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
    spu: Mapped[str] = mapped_column(String(32), default="", unique=True)  # 采集时自动赋予的管理编号 SPU{id:06d}
    source_platform: Mapped[str] = mapped_column(String(16), default="pdd")  # pdd / 1688
    source_goods_id: Mapped[str] = mapped_column(String(64))
    market_code: Mapped[str] = mapped_column(String(2), default="TH")
    product_line: Mapped[str] = mapped_column(String(32), default="accessories")
    title_cn: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    main_image_url: Mapped[str] = mapped_column(String(1000), default="")
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    cost_cny_used: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    cost_source: Mapped[str] = mapped_column(String(32), default="")  # sku_matrix/detail_price/search_buffered/fallback
    weight_g: Mapped[int] = mapped_column(Integer, default=80)
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True)  # pdd:{goods_id}
    status: Mapped[str] = mapped_column(String(16), default="ingested")  # discovered/ingested/listed/failed
    active: Mapped[bool] = mapped_column(default=True)  # 软删标记:False=已在采集表删除,不再出表/上架
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
    style_en: Mapped[str] = mapped_column(String(128), default="")  # 1688 中文款式 → 英文(上传表用)
    size: Mapped[str] = mapped_column(String(32), default="One Size")
    supplier_sku_id: Mapped[str] = mapped_column(String(64), default="")
    cost_cny: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
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
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)  # 展示价
    target_sale_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)  # 目标成交价
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    discount_rate: Mapped[float] = mapped_column(Numeric(4, 2), default=0.25)
    seller_sku: Mapped[str] = mapped_column(String(128), unique=True)
    listing_status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/ready/submitted/failed
    copy_source: Mapped[str] = mapped_column(String(64), default="")
    copy_reason: Mapped[str] = mapped_column(String(500), default="")
    copy_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sku_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_task_idempotency_key"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(32), default="ingest")  # collect/ingest/copy/upload/sync_orders/sync_messages
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/success/failed/waiting_human
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    owner: Mapped[str] = mapped_column(String(100), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ApprovalRun(Base):
    __tablename__ = "approval_runs"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decision: Mapped[str] = mapped_column(String(32), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    card_data: Mapped[dict] = mapped_column(JSON, default=dict)
    upload_status: Mapped[str] = mapped_column(String(16), default="")
    upload_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AccountApiCredential(Base):
    __tablename__ = "account_api_credentials"
    __table_args__ = (UniqueConstraint("account_id", "platform_api", name="uq_account_api"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"))
    platform_api: Mapped[str] = mapped_column(String(32), default="tiktok_open")
    app_key: Mapped[str] = mapped_column(String(255), default="")
    app_secret: Mapped[str] = mapped_column(String(255), default="")
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class UploadResult(Base):
    __tablename__ = "upload_results"
    __table_args__ = (UniqueConstraint("listing_id", "processed_by", name="uq_upload_processed"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listings.id", ondelete="CASCADE"))
    account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_code: Mapped[str] = mapped_column(String(2), default="TH")
    row_status: Mapped[str] = mapped_column(String(16), default="error")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(String(500), default="")
    error_category: Mapped[str] = mapped_column(String(64), default="")
    processed_by: Mapped[str] = mapped_column(String(128), default="")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("account_id", "order_no", name="uq_account_order"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    order_no: Mapped[str] = mapped_column(String(64))
    market_code: Mapped[str] = mapped_column(String(2), default="TH")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    buyer_note: Mapped[str] = mapped_column(String(500), default="")
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    ship_by_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_source: Mapped[str] = mapped_column(String(16), default="api")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"))
    seller_sku: Mapped[str] = mapped_column(String(128), default="")
    product_title: Mapped[str] = mapped_column(String(300), default="")
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    thread_id: Mapped[str] = mapped_column(String(128), default="")
    external_message_id: Mapped[str] = mapped_column(String(128), unique=True)
    direction: Mapped[str] = mapped_column(String(8), default="in")
    from_user: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    intent: Mapped[str] = mapped_column(String(32), default="")
    ai_reply_draft: Mapped[str] = mapped_column(Text, default="")
    reply_status: Mapped[str] = mapped_column(String(16), default="none")
    sync_source: Mapped[str] = mapped_column(String(16), default="api")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class MessageAttachment(Base):
    __tablename__ = "message_attachments"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"))
    media_type: Mapped[str] = mapped_column(String(32), default="image")
    url: Mapped[str] = mapped_column(String(1000), default="")
    local_path: Mapped[str] = mapped_column(String(500), default="")
    mime: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ImageQaRecord(Base):
    __tablename__ = "image_qa_records"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    ref_type: Mapped[str] = mapped_column(String(16))
    ref_id: Mapped[int] = mapped_column(BigInteger)
    image_url: Mapped[str] = mapped_column(String(1000), default="")
    image_path: Mapped[str] = mapped_column(String(500), default="")
    rule_checks: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_checks: Mapped[dict] = mapped_column(JSON, default=dict)
    overall: Mapped[str] = mapped_column(String(16), default="review")
    fail_reasons: Mapped[list] = mapped_column(JSON, default=list)
    qa_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(String(1000), default="")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SyncWatermark(Base):
    __tablename__ = "sync_watermarks"
    __table_args__ = (UniqueConstraint("account_id", "sync_type", name="uq_sync_watermark"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"))
    sync_type: Mapped[str] = mapped_column(String(32))
    last_key: Mapped[str] = mapped_column(String(128), default="")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SkippedProduct(Base):
    __tablename__ = "skipped_products"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("products.id"), nullable=True)
    source_platform: Mapped[str] = mapped_column(String(16), default="")
    source_goods_id: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    skipped_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DedupRegistry(Base):
    __tablename__ = "dedup_registry"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True)
    product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("products.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="discovered")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    action: Mapped[str] = mapped_column(String(64), default="")
    object_type: Mapped[str] = mapped_column(String(32), default="")
    object_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
