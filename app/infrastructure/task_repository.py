from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Task


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session, *, lease_seconds: int = 900):
        self.session = session
        self.lease_seconds = lease_seconds

    def create(self, task_type: str) -> Task:
        task = Task(task_type=task_type, status="pending")
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def get(self, task_id: int) -> Task:
        task = self.session.get(Task, task_id)
        if task is None:
            raise LookupError(f"task {task_id} not found")
        return task

    def claim_pending(self, *, owner: str) -> Task | None:
        task = self.session.execute(
            select(Task).where(Task.status == "pending").order_by(Task.id).limit(1)
        ).scalar_one_or_none()
        if task is None:
            return None
        task.status = "running"
        task.owner = owner
        task.attempts += 1
        task.started_at = datetime.now(timezone.utc)
        task.lease_until = task.started_at + timedelta(seconds=self.lease_seconds)
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
        self.session.commit()
        self.session.refresh(task)
        return task
