from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    ping: Callable[[], bool] = request.app.state.database_ping
    try:
        ping()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "DATABASE_UNAVAILABLE", "message": str(exc)}},
        )
    return {"status": "ready"}
