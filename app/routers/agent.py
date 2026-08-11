"""POST /api/agent/run —— 浏览器/飞书触发单个环节(失败隔离,可逐环节演示)。

同步执行(sync def → FastAPI 线程池),claude -p 阻塞但 uvicorn 不卡。
stage 见 app.agent.tasks.STAGES(ping/collect/table/review/approval/upload/rag_check/rag_copy)。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..agent import approval, tasks

router = APIRouter()


class AgentRun(BaseModel):
    stage: str
    params: dict = {}


@router.post("/agent/run")
def run(payload: AgentRun):
    return tasks.run_stage(payload.stage, payload.params)


@router.get("/agent/approvals")
def approvals():
    """审批状态列表(演示看板用)。"""
    return {"runs": approval.list_runs()}
