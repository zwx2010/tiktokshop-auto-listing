from .services import ListingService
from ..domain.errors import InvalidStateTransition


def evaluate_publication_gates(
    *,
    copy_status: str,
    image_qa: str,
    approved: bool,
    account_integration_status: str = "confirmed",
) -> dict:
    """Return one auditable gate decision shared by submit paths."""
    failed_gates = []
    if copy_status != "ready":
        failed_gates.append("copy")
    if image_qa != "pass":
        failed_gates.append("image_qa")
    if not approved:
        failed_gates.append("approval")
    if account_integration_status != "confirmed":
        failed_gates.append("account_integration")
    return {"eligible": not failed_gates, "failed_gates": failed_gates}


class PublicationGate:
    """上架前确定性审核门：无文案、图片失败或未审批都不得提交。"""

    def check(
        self,
        *,
        copy_status: str,
        image_qa: str,
        approved: bool,
        account_integration_status: str = "confirmed",
    ) -> bool:
        result = evaluate_publication_gates(
            copy_status=copy_status,
            image_qa=image_qa,
            approved=approved,
            account_integration_status=account_integration_status,
        )
        if not result["eligible"]:
            raise InvalidStateTransition(
                "publication gates failed: " + ", ".join(result["failed_gates"])
            )
        return True
