"""可序列化的迁移报告。"""

from dataclasses import dataclass, field


@dataclass
class MigrationReport:
    tables: dict[str, dict[str, int]] = field(default_factory=dict)
    relation_failures: list[dict[str, str]] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)

    def record_table(self, table: str, *, read: int, inserted: int, skipped: int) -> None:
        self.tables[table] = {"read": read, "inserted": inserted, "skipped": skipped}

    def record_relation_failure(self, table: str, detail: str) -> None:
        self.relation_failures.append({"table": table, "detail": detail})

    def record_conflict(self, table: str, detail: str) -> None:
        self.conflicts.append({"table": table, "detail": detail})

    def to_dict(self) -> dict:
        return {"tables": self.tables, "relation_failures": self.relation_failures,
                "conflicts": self.conflicts}
