"""FastAPI 入口 —— 启动前自动建表。

运行：
    python -m uvicorn app.main:app --reload --port 8000
或：
    python -m app.main
"""
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .agent import approval
from .database import init_db
from .feishu import handlers
from .routers import agent, api, feishu, pages

app = FastAPI(title="TikTokShop 多账号自动化运营平台", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # 飞书审批回调接线:卡片按钮/群消息 → 审批状态机(M3 编排)
    handlers.set_handlers(on_message=approval.on_message,
                          on_card_action=approval.on_card_action)


@app.get("/", include_in_schema=False)
def _root():
    return RedirectResponse(url="/dashboard/")


@app.get("/dashboard", include_in_schema=False)
def _dashboard():
    return RedirectResponse(url="/dashboard/")


app.include_router(pages.router, prefix="/dashboard", tags=["pages"])
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(agent.router, prefix="/api", tags=["agent"])
app.include_router(feishu.router, prefix="/api", tags=["feishu"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
