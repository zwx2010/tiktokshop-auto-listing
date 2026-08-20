from .services import ListingService
from ..domain.errors import InvalidStateTransition


class PublicationGate:
    """上架前确定性审核门：无文案、图片失败或未审批都不得提交。"""

    def check(self, *, copy_status: str, image_qa: str, approved: bool) -> bool:
        if copy_status != "ready":
            raise InvalidStateTransition("listing copy is not ready")
        if image_qa != "pass":
            raise InvalidStateTransition("listing image QA did not pass")
        if not approved:
            raise InvalidStateTransition("listing has not been approved")
        return True
