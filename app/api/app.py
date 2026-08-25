from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .health import router as health_router
from .tasks import router as task_router


def create_app(*, database_ping: Callable[[], bool] | None = None,
               start_worker: Callable[[], None] | None = None,
               task_repository_factory: Callable[[], object] | None = None) -> FastAPI:
    """创建 API-only 应用；worker 只有显式传入启动器时才运行。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.database_ping = database_ping or _default_database_ping
        app.state.worker_started = False
        app.state.task_repository_factory = task_repository_factory or _default_task_repository
        if start_worker is not None:
            start_worker()
            app.state.worker_started = True
        yield

    app = FastAPI(
        title="TikTokShop 多账号自动化运营平台",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(task_router, prefix="/api/v1")
    return app


def _default_database_ping() -> bool:
    from ..database import ping

    return ping()


def _default_task_repository():
    from ..database import SessionLocal
    from ..infrastructure.task_repository import SqlAlchemyTaskRepository

    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL must point to MySQL before creating tasks")
    return SqlAlchemyTaskRepository(SessionLocal())


app = create_app()
