# Batch 1 Review

## Scope

Tasks 1-3：基线、MySQL 数据层、Alembic 骨架和 SQLite→MySQL 迁移工具。

## Evidence

- `python -m unittest discover -s tests -p 'test_*.py'`：6 passed。
- `python -X pycache_prefix=... -m compileall -q app tools tests migrations`：通过。
- `python tools/migrate_sqlite_to_mysql.py --help`：通过，CLI 可从项目根目录外直接加载。
- schema contract test：19 张目标业务表均存在。
- money contract test：金额字段使用 `sqlalchemy.Numeric`。
- SQLite runtime contract：SQLite URL 在业务数据库模块中抛出明确错误。

## Review Findings

- 无 Critical/Important findings。
- MySQL 实例连接和真实迁移报告尚未在当前环境执行；Batch 5 的部署/切换验证仍为强制门。
- 迁移工具当前按现有 SQLAlchemy metadata 建表；新增领域字段/表必须在后续 schema 变更中生成独立迁移版本，不得绕过 Alembic。

## Verdict

PASS — Batch 1 可进入 Batch 2。依赖条件：后续批次继续使用统一 Repository/Unit of Work，不能重新引入 SQLite 业务运行时或直接 `SessionLocal` 写业务逻辑。
