from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import UploadAuthorization


class UploadAuthorizationRepository:
    def __init__(self, session: Session):
        self.session = session

    def authorize(self, batch_id: str, *, command: str = "") -> UploadAuthorization:
        row = self.session.execute(
            select(UploadAuthorization).where(UploadAuthorization.batch_id == batch_id)
        ).scalar_one_or_none()
        if row is None:
            row = UploadAuthorization(batch_id=batch_id, command=command[:500])
            self.session.add(row)
            self.session.commit()
            self.session.refresh(row)
        return row

    def is_authorized(self, batch_id: str) -> bool:
        return self.session.execute(
            select(UploadAuthorization.id).where(UploadAuthorization.batch_id == batch_id)
        ).scalar_one_or_none() is not None
