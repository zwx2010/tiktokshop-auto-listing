"""显式 worker 入口；API 进程不会隐式启动它。"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.jobs.worker import TaskWorker


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the explicit TikTokShop task worker")
    parser.add_argument("--once", action="store_true", help="run one pending task and exit")
    parser.parse_args()
    # 具体任务处理器在 Batch 5 由 MySQL task repository 和配置注入。
    worker = TaskWorker(handlers={})
    worker.run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
