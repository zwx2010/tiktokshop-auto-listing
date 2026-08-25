from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Task, TaskLog


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session, *, lease_seconds: int = 900):
        self.session = session
        self.lease_seconds = lease_seconds

    def create(self, task_type: str, *, idempotency_key: str | None = None,
               state: dict | None = None) -> Task:
        if idempotency_key:
            existing = self.session.execute(
                select(Task).where(Task.idempotency_key == idempotency_key)
            ).scalar_one_or_none()
            if existing is not None:
                return existing
        task = Task(task_type=task_type, status="pending",
                    idempotency_key=idempotency_key, state=state or {})
        self.session.add(task)
        self.session.flush()
        self.session.add(TaskLog(task_id=task.id, message="created"))
        self.session.commit()
        self.session.refresh(task)
        return task

    def get(self, task_id: int) -> Task:
        task = self.session.get(Task, task_id)
        if task is None:
            raise LookupError(f"task {task_id} not found")
        return task

    def claim_pending(self, *, owner: str) -> Task | None:
        now = datetime.now(timezone.utc)
        task = self.session.execute(
            select(Task).where(
                or_(
                    Task.status == "pending",
                    (Task.status == "running") & (Task.lease_until <= now),
                )
            ).order_by(Task.id).limit(1).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if task is None:
            return None
        task.status = "running"
        task.owner = owner
        task.attempts += 1
        task.started_at = now
        task.lease_until = now + timedelta(seconds=self.lease_seconds)
        self.session.add(TaskLog(task_id=task.id, message=f"claimed:{owner}"))
        self.session.commit()
        self.session.refresh(task)
        return task

    def finish(self, task: Task, *, owner: str, success: bool, error: str = "") -> Task:
        if task.status != "running" or task.owner != owner:
            raise PermissionError("only the current task owner can finish the task")
        task.status = "success" if success else "failed"
        task.error_message = error
        task.finished_at = datetime.now(timezone.utc)
        task.lease_until = None
        level = "info" if success else "error"
        self.session.add(TaskLog(task_id=task.id, level=level,
                                 message="finished:success" if success else f"failed:{error}"))
        self.session.commit()
        self.session.refresh(task)
        return task
