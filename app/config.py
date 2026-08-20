"""全局配置。业务运行时只允许使用 MySQL。"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

def database_url_from_environment(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return source.get("DATABASE_URL", "").strip()


def validate_runtime_database_url(url: str) -> bool:
    normalized = (url or "").strip().lower()
    if not normalized.startswith("mysql+"):
        raise ValueError("业务运行时数据库必须是 MySQL；SQLite 仅允许作为迁移输入")
    return True


DATABASE_URL = database_url_from_environment()

DEFAULT_STORE_NAME = "RoseSeek"
DEFAULT_PRODUCT_LINE = "accessories"
DEFAULT_MARKET = "TH"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def get_market_pricing() -> dict:
    return load_json(CONFIG_DIR / "market_pricing.json")


def get_market_localization() -> dict:
    return load_json(CONFIG_DIR / "market_listing_localization.json")
