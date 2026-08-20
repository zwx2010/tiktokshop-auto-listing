"""FastAPI 入口 —— 启动前自动建表。

运行：
    python -m uvicorn app.main:app --reload --port 8000
或：
    python -m app.main
"""
import threading
import time

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .agent import approval
from .api.app import create_app
from .database import init_db
from .feishu import handlers
from .routers import agent, api, feishu, pages

app = create_app()

# ---------------------------------------------------------------- 多维表格轮询
# 只开一条流水线(_busy),避免两个批次同时抢同一个 CDP 浏览器(端口 9344/9223)冲突。
# 拿行即翻:读到「待上架」立刻翻成「处理中」,下次轮询查不到 → 防重复。
POLL_INTERVAL_S = 45
STALE_LOCK_MS = 30 * 60 * 1000
_STALE_STATUSES = ["处理中", "文案生成中", "图片质检中", "审批中", "上架中"]
_busy = threading.Event()


def _recover_stale() -> None:
    """把进程崩溃留下的超时锁回滚成「待上架」。不写这个,崩一次整池货静默不动。"""
    from .feishu import bitable
    try:
        stale = bitable.stale_locked(_STALE_STATUSES, STALE_LOCK_MS)
        if stale:
            bitable.batch_update([{"record_id": r["record_id"],
                                   "fields": {"状态": "待上架",
                                              "备注": "锁定超时,已重置待重试"}}
                                  for r in stale])
            print(f"[bitable] 回滚 {len(stale)} 行超时锁(>{STALE_LOCK_MS // 60000}min)",
                  flush=True)
    except Exception as exc:
        print(f"[bitable-stale] {exc}", flush=True)


def _run_batch(rows) -> None:
    try:
        from .agent import bitable_flow
        bitable_flow.run_picked(rows)
    finally:
        _busy.clear()


def _poll_loop() -> None:
    while True:
        try:
            if not _busy.is_set():
                from .feishu import bitable
                rows = bitable.pickup_pending("待上架", "处理中")
                if rows:
                    _busy.set()
                    threading.Thread(target=_run_batch, args=(rows,),
                                     daemon=True).start()
                else:
                    _recover_stale()
        except Exception as exc:
            print(f"[bitable-poll] {exc}", flush=True)
        time.sleep(POLL_INTERVAL_S)


def _start_bitable_poll() -> None:
    from .feishu import bitable
    if not bitable.is_configured():
        print("[bitable] 未配置多维表格(app_token/table_id),轮询不启动;"
              "现有飞书消息流程不受影响", flush=True)
        return
    threading.Thread(target=_poll_loop, daemon=True).start()
    print(f"[bitable] 轮询已启动: 每{POLL_INTERVAL_S}s 扫「待上架」行 → 真实流水线",
          flush=True)


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
