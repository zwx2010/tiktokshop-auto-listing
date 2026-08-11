"""重建 RAG 文案向量索引(幂等,可重复跑)。

用法(平台根目录下):
  python -m scripts.build_rag_index            # 语料没变则跳过
  python -m scripts.build_rag_index --force    # 强制重建
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag import build_index  # noqa: E402


def main():
    force = "--force" in sys.argv
    n, backend = build_index(force=force)
    print("索引就绪:%d 条范例,backend=%s%s"
          % (n, backend, " (强制重建)" if force else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
