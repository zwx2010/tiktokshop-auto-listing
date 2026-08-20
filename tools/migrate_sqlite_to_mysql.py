"""将 SQLite 业务库只读迁移到 MySQL。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import MetaData, create_engine, inspect, insert, select

# 允许从项目根目录之外直接执行 `python tools/migrate_sqlite_to_mysql.py`。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import validate_runtime_database_url
from app.database import Base
from app.migration.plan import classify_row, coerce_explicit_null, ordered_tables
from app.migration.report import MigrationReport


def _table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [row[0] for row in rows]


def _source_rows(connection: sqlite3.Connection, table: str) -> list[dict]:
    quoted = '"' + table.replace('"', '""') + '"'
    cursor = connection.execute(f"SELECT * FROM {quoted}")
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def migrate(source: Path, target_url: str, report_path: Path, *, batch_size: int = 500) -> dict:
    validate_runtime_database_url(target_url)
    if not source.exists():
        raise FileNotFoundError(source)

    target_engine = create_engine(target_url, pool_pre_ping=True)
    Base.metadata.create_all(bind=target_engine)
    target_metadata = MetaData()
    target_metadata.reflect(bind=target_engine)
    target_names = set(inspect(target_engine).get_table_names())
    report = MigrationReport()

    with sqlite3.connect(source) as source_connection, target_engine.begin() as target_connection:
        source_names = set(_table_names(source_connection))
        for table_name in ordered_tables(target_metadata, source_names):
            rows = _source_rows(source_connection, table_name)
            if table_name not in target_names:
                report.record_table(table_name, read=len(rows), inserted=0, skipped=0)
                report.record_relation_failure(table_name, "target table is not present in current schema")
                continue

            target_table = target_metadata.tables[table_name]
            primary_keys = [column.name for column in target_table.primary_key.columns]
            inserted_count = 0
            skipped_count = 0
            for offset in range(0, len(rows), batch_size):
                for row in rows[offset:offset + batch_size]:
                    values = {
                        key: coerce_explicit_null(table_name, key, value)
                        for key, value in row.items()
                        if key in target_table.c
                    }
                    existing = False
                    if primary_keys and all(key in values for key in primary_keys):
                        predicate = [target_table.c[key] == values[key] for key in primary_keys]
                        existing = target_connection.execute(
                            select(target_table).where(*predicate).limit(1)
                        ).first() is not None
                    decision = classify_row(existing=existing, conflict=False)
                    if decision == "skip_existing":
                        skipped_count += 1
                        continue
                    try:
                        target_connection.execute(insert(target_table).values(values))
                        inserted_count += 1
                    except Exception as exc:
                        report.record_conflict(table_name, str(exc))
                        raise
            report.record_table(table_name, read=len(rows), inserted=inserted_count, skipped=skipped_count)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite business data to MySQL")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.target, args.report, batch_size=args.batch_size),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
