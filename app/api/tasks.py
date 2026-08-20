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
    task = request.app.state.task_service.create(task_type=payload.task_type)
    return TaskResponse(id=task.id, task_type=task.task_type, status=task.status,
                        attempts=task.attempts)
