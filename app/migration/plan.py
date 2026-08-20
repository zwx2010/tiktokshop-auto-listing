"""迁移幂等判定。"""


def classify_row(*, existing: bool, conflict: bool) -> str:
    if conflict:
        return "conflict"
    if existing:
        return "skip_existing"
    return "insert"
