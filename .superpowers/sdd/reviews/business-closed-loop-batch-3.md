# Batch 3 Review — Copy Recovery

## Scope Reviewed

- Auditable copy fields on Listing.
- Shared copy result persistence helper.
- Pipeline and recovery script integration.
- Alembic migration `0003_listing_copy_audit`.
- Listing API/dashboard visibility for copy source and reason.

## Evidence

- RED: `pytest -q tests/test_copy_recovery.py -p no:cacheprovider` failed before the recovery helper existed.
- Focused copy tests: 2 passed.
- Full suite: 30 passed, 1 existing Starlette/httpx deprecation warning.
- `alembic upgrade head` against MySQL succeeded; current revision is `0003_listing_copy_audit (head)`.
- Recovery dry-run found 45 listings needing copy and 0 codex-covered products.
- Safe local `--mode auto` run made no external AI calls and recorded all 45 as `copy_missing` with reason `no configured real copy provider`.
- After restart, `/api/listings` exposes `copy_source`, `copy_reason`, and `copy_checked_at`.
- No fake title or description was generated.

## Review Result

PASS. Copy recovery is traceable and fail-closed; missing providers keep listings blocked rather than fabricating copy.

## Deferred Compatibility Finding

`alembic upgrade head --sql` still fails in the pre-existing `0002_task_leases` migration because it calls SQLAlchemy inspection in offline mode. Online MySQL upgrade and the new `0003` migration pass. This is carried into Batch 5 regression work.
