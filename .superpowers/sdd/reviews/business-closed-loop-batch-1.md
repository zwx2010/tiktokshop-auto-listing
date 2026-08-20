# Batch 1 Review — Business Closed Loop Hardening

## Scope Reviewed

- Shared active-product metric query.
- `/api/stats` alignment with dashboard semantics.
- Dashboard runtime label correction.
- Baseline audit and focused regression tests.

## Evidence

- RED: `pytest -q tests/test_metrics_consistency.py -p no:cacheprovider` failed before `app/metrics.py` existed.
- GREEN: focused tests passed: 2 passed.
- Full suite: 25 passed, 1 existing Starlette/httpx deprecation warning.
- Live MySQL service: `/api/v1/health/ready` returned 200.
- Live `/api/stats`: `products=186`, matching the active dashboard count.
- Live dashboard contains `FastAPI + MySQL` and no longer contains `Python + SQLite`.
- `git diff --check`: passed.

## Review Result

PASS. The implementation is within Batch 1 scope, reuses one metric definition in both routes, preserves the existing database schema, and does not change listing or external-provider data.

## Minor Note

The existing TestClient deprecation warning remains non-blocking and is outside this batch's scope.
