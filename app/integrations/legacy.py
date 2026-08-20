"""把现有真实集成包装成 ports，避免领域层直接依赖旧模块。"""

from .errors import ExternalError


class LegacyCopyGateway:
    def __init__(self, client):
        self.client = client

    def generate(self, product: dict, market_code: str) -> dict:
        try:
            result = self.client.generate_copy(product, market_code)
        except Exception as exc:
            raise ExternalError(str(exc), retryable=False) from exc
        if not result.source or not result.title or not result.description:
            return {"status": "copy_missing", "source": result.source}
        return {"status": "ready", "title": result.title,
                "description": result.description, "keywords": result.keywords,
                "styles_en": result.styles_en, "source": result.source}


class DeterministicImageQaGateway:
    """确定性基础门；AI 质检结果由后续适配器补充。"""

    def check(self, image_urls: list[str], market_code: str) -> dict:
        if not image_urls:
            return {"overall": "fail", "reasons": ["no_images"], "market_code": market_code}
        return {"overall": "pass", "reasons": [], "market_code": market_code}


class LegacyFeishuGateway:
    def send_approval(self, payload: dict) -> str:
        from ..feishu import client

        try:
            result = client.deliver_card(payload["card"])
        except Exception as exc:
            raise ExternalError(str(exc), retryable=True) from exc
        if not result:
            raise ExternalError("Feishu approval card was not delivered", retryable=True)
        return str(result.get("message_id") or result.get("id") or "delivered")

    def parse_callback(self, payload: dict) -> dict:
        from ..feishu import handlers

        try:
            return handlers.handle_card_action(payload)
        except Exception as exc:
            raise ExternalError(str(exc), retryable=False) from exc


class LegacyCdpGateway:
    def submit_listing(self, payload: dict) -> dict:
        from ..agent import tasks

        try:
            result = tasks.run_stage("upload", payload, timeout_s=900)
        except Exception as exc:
            raise ExternalError(str(exc), retryable=True) from exc
        if not result or not result.get("ok"):
            raise ExternalError(str((result or {}).get("error") or "CDP upload failed"), retryable=False)
        return result
