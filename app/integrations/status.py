"""Read-only integration readiness checks."""

import json
import os
from pathlib import Path

from ..config import CONFIG_DIR
from ..models import AccountApiCredential
from .outcomes import IntegrationOutcome, credential_status


def _feishu_config() -> dict:
    path = Path(CONFIG_DIR) / "feishu.local.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def integration_statuses(db, env: dict[str, str] | None = None) -> list[IntegrationOutcome]:
    source = os.environ if env is None else env
    feishu = _feishu_config()
    credentials = db.query(AccountApiCredential).all() if db is not None else []
    tiktok_configured = any(
        bool(row.app_key and row.app_secret and row.access_token) for row in credentials
    )

    tiktok = credential_status(
        "tiktok",
        "account_api",
        {"account_api_credentials": "configured" if tiktok_configured else ""},
    )
    ai = credential_status(
        "ai",
        "copy_generation",
        {
            "COZE_API_TOKEN": source.get("COZE_API_TOKEN", ""),
            "COZE_BOT_ID": source.get("COZE_BOT_ID", ""),
        },
    )
    feishu = credential_status(
        "feishu",
        "approval_delivery",
        {
            "app_id_or_webhook": feishu.get("app_id") or feishu.get("group_bot_webhook", ""),
            "app_secret": feishu.get("app_secret") or "webhook-mode",
        },
    )
    cdp = credential_status(
        "cdp",
        "browser_upload",
        {
            "CDP_ENDPOINT": source.get("CDP_ENDPOINT") or source.get("CDP_URL", ""),
        },
    )
    return [tiktok, ai, feishu, cdp]
