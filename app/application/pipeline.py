from __future__ import annotations

from dataclasses import dataclass

from ..integrations.ports import CdpGateway, CopyGenerator, FeishuGateway, ImageQaGateway
from .workflow import PublicationGate


@dataclass
class ListingWorkflow:
    copy: CopyGenerator
    image_qa: ImageQaGateway
    feishu: FeishuGateway
    cdp: CdpGateway

    def __post_init__(self) -> None:
        self.gate = PublicationGate()

    def run(self, product: dict) -> dict:
        market_code = product["market_code"]
        copy_result = self.copy.generate(product, market_code)
        if copy_result.get("status") != "ready":
            return {"status": "blocked", "reason": "copy_missing", "copy": copy_result}

        qa_result = self.image_qa.check(product.get("image_urls", []), market_code)
        if qa_result.get("overall") != "pass":
            return {"status": "blocked", "reason": "image_qa", "image_qa": qa_result}

        approval_id = self.feishu.send_approval(
            {"product": product, "copy": copy_result, "image_qa": qa_result}
        )
        return {"status": "waiting_approval", "approval_id": approval_id,
                "copy": copy_result, "image_qa": qa_result}

    def submit_after_approval(self, payload: dict) -> dict:
        self.gate.check(
            copy_status=payload["copy"]["status"],
            image_qa=payload["image_qa"]["overall"],
            approved=payload.get("approved", False),
            account_integration_status=payload.get("account_integration_status", "not_configured"),
        )
        return self.cdp.submit_listing(payload)
