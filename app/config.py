"""全局配置。

默认使用 SQLite，一行切换到 MySQL：
    export DATABASE_URL=mysql+pymysql://user:pass@localhost:3306/tiktok_platform
SQLAlchemy 模型不变，仅连接串变化 —— 面试时可演示这个切换成本。
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{DATA_DIR / 'platform.db'}",
)

DEFAULT_STORE_NAME = "RoseSeek"
DEFAULT_PRODUCT_LINE = "accessories"
DEFAULT_MARKET = "TH"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def get_market_pricing() -> dict:
    return load_json(CONFIG_DIR / "market_pricing.json")


def get_market_localization() -> dict:
    return load_json(CONFIG_DIR / "market_listing_localization.json")
