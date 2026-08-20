from __future__ import annotations

import threading
from collections.abc import Callable

from ..application.services import TaskService
from ..domain.entities import TaskAggregate


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
