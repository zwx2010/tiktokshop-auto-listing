from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import UploadAuthorization


class UploadAuthorizationRepository:
    def __init__(self, session: Session):
        self.session = session

    def authorize(self, batch_id: str, *, record_ids: list[str],
                  command: str = "") -> UploadAuthorization:
        row = self.session.execute(
            select(UploadAuthorization).where(UploadAuthorization.batch_id == batch_id)
        ).scalar_one_or_none()
        if row is None:
            row = UploadAuthorization(batch_id=batch_id,
                                      record_ids=sorted({str(x) for x in record_ids if x}),
                                      command=command[:500])
            self.session.add(row)
            self.session.commit()
            self.session.refresh(row)
        return row

    def is_authorized(self, batch_id: str, record_ids: list[str]) -> bool:
        row = self.session.execute(
            select(UploadAuthorization.id).where(UploadAuthorization.batch_id == batch_id)
        ).scalar_one_or_none()
        if row is None:
            return False
        auth = self.session.get(UploadAuthorization, row)
        return bool(auth and set(str(x) for x in record_ids if x)
                    and set(str(x) for x in record_ids if x).issubset(set(auth.record_ids or [])))
