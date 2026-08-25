from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreateRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=32)


class TaskResponse(BaseModel):
    id: int
    task_type: str
    status: str
    attempts: int


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreateRequest, request: Request) -> TaskResponse:
    repository = request.app.state.task_repository_factory()
    try:
        idempotency_key = request.headers.get("Idempotency-Key")
        task = repository.create(payload.task_type, idempotency_key=idempotency_key)
        return TaskResponse(id=task.id, task_type=task.task_type, status=task.status,
                            attempts=task.attempts)
    finally:
        repository.session.close()
