"""显式 worker 入口；API 进程不会隐式启动它。"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.jobs.worker import PersistentTaskWorker


def build_worker(*, session_factory=None, handlers=None, owner="worker-1"):
    """Build the production persistent worker with injectable test boundaries."""
    factory = session_factory or SessionLocal
    if factory is None:
        raise SystemExit("DATABASE_URL must point to MySQL before starting the worker")
    if handlers is None:
        from app.agent import tasks

        handlers = {
            "upload": lambda task: tasks.run_stage(
                "upload", task.state or {}, timeout_s=900
            )
        }
    return PersistentTaskWorker(
        session_factory=factory,
        handlers=handlers,
        owner=owner,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the explicit TikTokShop task worker")
    parser.add_argument("--once", action="store_true", help="run one pending task and exit")
    parser.parse_args()
    build_worker().run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
