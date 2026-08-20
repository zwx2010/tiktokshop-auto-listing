"""数据库引擎与 Session。"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL, validate_runtime_database_url

class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = None
if DATABASE_URL:
    validate_runtime_database_url(DATABASE_URL)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800,
                           pool_size=5, max_overflow=10, echo=False)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _require_engine():
    if engine is None or SessionLocal is None:
        raise RuntimeError("未配置 DATABASE_URL；业务运行时必须提供 MySQL 连接串")
    return engine


def init_db() -> None:
    from . import models  # noqa: F401  确保模型注册进 Base.metadata

    Base.metadata.create_all(bind=_require_engine())


def ping() -> bool:
    with _require_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    return True


def get_db():
    _require_engine()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
