from __future__ import annotations

import threading
from collections.abc import Callable

from ..application.services import TaskService
from ..domain.entities import TaskAggregate
from ..infrastructure.task_repository import SqlAlchemyTaskRepository


class TaskWorker:
    """单进程单批次 worker；后续由 MySQL 租约替换内存任务队列。"""

    def __init__(self, *, handlers: dict[str, Callable[[TaskAggregate], None]], owner: str = "worker-1"):
        self.service = TaskService()
        self.handlers = handlers
        self.owner = owner
        self._lock = threading.Lock()

    def add_task(self, task_type: str) -> TaskAggregate:
        return self.service.create(task_type=task_type)

    def run_once(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            task = next((item for item in self.service.tasks if item.status == "pending"), None)
            if task is None:
                return False
            self.service.claim(task, owner=self.owner)
            try:
                handler = self.handlers[task.task_type]
                handler(task)
            except Exception as exc:
                task.status = "failed"
                task.logs.append(f"failed:{exc}")
            else:
                self.service.finish(task, owner=self.owner)
            return True
        finally:
            self._lock.release()


class PersistentTaskWorker:
    """MySQL task/task_logs 的最小 worker 循环。"""

    def __init__(self, *, session_factory, handlers: dict[str, Callable], owner: str = "worker-1"):
        self.session_factory = session_factory
        self.handlers = handlers
        self.owner = owner

    def run_once(self) -> bool:
        session = self.session_factory()
        try:
            repo = SqlAlchemyTaskRepository(session)
            task = repo.claim_pending(owner=self.owner)
            if task is None:
                return False
            try:
                self.handlers[task.task_type](task)
            except Exception as exc:
                repo.finish(task, owner=self.owner, success=False, error=str(exc))
            else:
                repo.finish(task, owner=self.owner, success=True)
            return True
        finally:
            session.close()
