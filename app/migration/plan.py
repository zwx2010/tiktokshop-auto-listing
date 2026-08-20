"""迁移幂等判定。"""


def classify_row(*, existing: bool, conflict: bool) -> str:
    if conflict:
        return "conflict"
    if existing:
        return "skip_existing"
    return "insert"


def ordered_tables(target_metadata, source_names: set[str]) -> list[str]:
    """按外键依赖顺序返回迁移表，避免子表先于父表写入。"""
    ordered = [table.name for table in target_metadata.sorted_tables if table.name in source_names]
    known = set(ordered)
    return ordered + sorted(source_names - known)


NULL_DEFAULTS = {
    "product_skus.style_en": "",
}


def coerce_explicit_null(table_name: str, column_name: str, value):
    """只应用经过业务确认的 NULL 映射，未知 NULL 保持原值并让数据库拒绝。"""
    if value is None:
        return NULL_DEFAULTS.get(f"{table_name}.{column_name}")
    return value
