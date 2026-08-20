# Tasks

## Delivery and Proof Map

| Requirement | Delivery | Proof |
|---|---|---|
| Honest external integration status | typed provider outcomes and configuration checks | missing-credential, timeout, invalid-response tests |
| Consistent dashboard/API metrics | shared active-product query and MySQL label | route/template tests and live API comparison |
| Listing copy recovery and gating | copy recovery service plus persisted reason/source | copy success/failure tests and listing audit |
| Unified pre-publish gates | central eligibility policy used by export/preparation | gate matrix tests and export verification |
| Regression-safe MySQL operation | preserve ORM/migration boundaries | pytest, Alembic, health, FK audit |

## Implementation Tasks

1. **Baseline and state audit** — inspect current routes, models, integrations, exports, and live MySQL lifecycle distributions. Evidence: a reproducible audit report and focused failing tests for the observed metric/copy issues. Depends on none.
2. **Metric and dashboard consistency** — update the shared active-product count, MySQL runtime label, and API response semantics. Affected areas: `app/routers/api.py`, `app/routers/pages.py`, `app/templates/index.html`. Evidence: API/template tests and live count comparison. Depends on 1.
3. **Typed provider outcome model** — define configuration, unavailable, failed, and confirmed-success outcomes for TikTok, AI, Feishu, and CDP adapters. Affected areas: `app/integrations/`, `app/application/`, configuration and tests. Evidence: deterministic missing-credential, timeout, and invalid-response tests. Depends on 1.
4. **Copy recovery workflow** — implement traceable `copy_missing` recovery using configured real providers, with safe failure persistence and content-rule validation. Affected areas: `app/agent/`, `app/pipeline.py`, copy scripts, models if required. Evidence: success/failure/retry tests and live status audit. Depends on 3.
5. **Unified publish-preparation gates** — centralize copy, image, approval, and account integration checks and apply them to export and preparation endpoints. Affected areas: application services, API routes, export code. Evidence: gate matrix and exported-row assertions. Depends on 2, 3, 4.
6. **MySQL compatibility and regression verification** — add migrations only if necessary, run existing tests, health checks, migration idempotency and foreign-key audits. Evidence: pytest, Alembic head, live MySQL checks, and final verification report. Depends on 5.
7. **Operational diagnostics and documentation** — document required credentials, failure states, retry boundaries, and operator-visible remediation. Affected areas: README, launcher, API docs. Evidence: documentation review and smoke test. Depends on 3, 5.
