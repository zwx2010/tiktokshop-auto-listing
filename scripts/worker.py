"""显式 worker 入口；API 进程不会隐式启动它。"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.jobs.worker import PersistentTaskWorker


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the explicit TikTokShop task worker")
    parser.add_argument("--once", action="store_true", help="run one pending task and exit")
    parser.parse_args()
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL must point to MySQL before starting the worker")
    from app.agent import tasks

    worker = PersistentTaskWorker(
        session_factory=SessionLocal,
        handlers={"upload": lambda task: tasks.run_stage("upload", task.state or {}, timeout_s=900)},
    )
    worker.run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
