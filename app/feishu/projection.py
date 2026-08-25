"""Database-first projection of task/approval facts into Feishu Bitable."""
from __future__ import annotations

import time

from ..infrastructure.approval_repository import ApprovalRunRepository
from ..infrastructure.task_repository import SqlAlchemyTaskRepository


def _write(writer, record_id, fields, retries=3):
    last = None
    for attempt in range(retries):
        try:
            writer(record_id, fields)
            return {"ok": True, "attempts": attempt + 1}
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(0.05 * (2 ** attempt))
    return {"ok": False, "attempts": retries, "error": str(last)}


def project_task(task_id, record_id, *, session, writer):
    task = SqlAlchemyTaskRepository(session).get(task_id)
    fields = {"任务ID": str(task.id), "任务类型": task.task_type,
              "状态": task.status, "尝试次数": task.attempts,
              "失败原因": task.error_message or ""}
    return _write(writer, record_id, fields)


def project_approval(run_id, record_id, *, session, writer):
    row = ApprovalRunRepository(session).get(run_id)
    if row is None:
        return {"ok": False, "error": f"approval run {run_id} not found"}
    fields = {"审批批次": row.run_id, "审批状态": row.status,
              "审批决定": row.decision or "", "上架状态": row.upload_status or "",
              "失败原因": row.last_error or ""}
    return _write(writer, record_id, fields)
