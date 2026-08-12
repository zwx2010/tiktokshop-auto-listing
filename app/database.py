"""数据库引擎与 Session。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

_connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite 默认不允许跨线程使用连接，FastAPI 多线程下必须开启
    _connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=_connect_args, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _ensure_active_column() -> None:
    """SQLite create_all 不改已存在表;旧库 products 补 active 软删列(照 backfill_spu 模式)。"""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text
    insp = sa_inspect(engine)
    cols = [c["name"] for c in insp.get_columns("products")]
    if "active" not in cols:
        with engine.begin() as con:
            con.execute(text("ALTER TABLE products ADD COLUMN active INTEGER DEFAULT 1"))


def init_db() -> None:
    from . import models  # noqa: F401  确保模型注册进 Base.metadata

    Base.metadata.create_all(bind=engine)
    _ensure_active_column()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
