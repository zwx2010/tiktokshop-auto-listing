from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApprovalAudit, ApprovalRun


class ApprovalRunRepository:
    """Persistent approval state with a database-locked transition."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, run_id: str, params: dict | None = None,
               card_data: dict | None = None) -> ApprovalRun:
        existing = self.get(run_id)
        if existing is not None:
            return existing
        row = ApprovalRun(run_id=run_id, params=params or {}, card_data=card_data or {})
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get(self, run_id: str) -> ApprovalRun | None:
        return self.session.execute(
            select(ApprovalRun).where(ApprovalRun.run_id == run_id)
        ).scalar_one_or_none()

    def list_all(self) -> list[ApprovalRun]:
        return list(self.session.execute(
            select(ApprovalRun).order_by(ApprovalRun.created_at.desc())
        ).scalars())

    def update_params(self, run_id: str, params: dict) -> None:
        row = self.get(run_id)
        if row is None:
            return
        row.params = params or {}
        row.updated_at = datetime.now(timezone.utc)
        self.session.commit()

    def set_upload_state(self, run_id: str, status: str, result=None,
                         error: str = "") -> None:
        row = self.get(run_id)
        if row is None:
            return
        row.upload_status = status
        if result is not None:
            row.upload_result = result
        row.last_error = error or ""
        row.updated_at = datetime.now(timezone.utc)
        self.session.commit()

    def transition(self, run_id: str, decision: str) -> dict | None:
        row = self.session.execute(
            select(ApprovalRun).where(ApprovalRun.run_id == run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.status != "pending":
            self.session.add(ApprovalAudit(run_id=run_id, event="duplicate_callback",
                                           decision=decision, result=f"already_{row.status}"))
            self.session.commit()
            return None
        row.status = "approved" if decision != "reject" else "rejected"
        row.decision = decision
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(ApprovalAudit(run_id=run_id, event="transition",
                                       decision=decision, result=row.status))
        self.session.commit()
        self.session.refresh(row)
        return self.as_dict(row)

    @staticmethod
    def as_dict(row: ApprovalRun) -> dict:
        return {
            "run_id": row.run_id,
            "status": row.status,
            "decision": row.decision,
            "params": row.params or {},
            "card_data": row.card_data or {},
            "upload_status": row.upload_status,
            "upload_result": row.upload_result,
            "last_error": row.last_error,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }
