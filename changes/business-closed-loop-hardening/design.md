# Design

## Relevant Facts and Constraints

- The existing rewrite is FastAPI + SQLAlchemy + Alembic + MySQL and has already migrated business data.
- The dashboard currently filters active products while `/api/stats` counts all products.
- The dashboard subtitle still says SQLite.
- Existing Listing data contains both `ready` and `copy_missing` states.
- External credentials and provider environments are not guaranteed in local verification.

## Goals and Non-Goals

Goals are consistent metrics, safe listing gates, traceable copy recovery, and truthful external integration outcomes. Non-goals are simulated provider success, bypassing approval/content rules, or replacing real credentials with fixtures in production paths.

## Decisions

### Decision 1: Keep provider boundaries explicit

- Choice: route TikTok, AI, Feishu, and CDP operations through ports/adapters that return typed success and non-success outcomes.
- Rationale: the system must distinguish unavailable infrastructure from a real provider success.
- Alternatives: keep legacy implicit fallbacks; rejected because they can silently mark work successful.
- Consequences: local tests need deterministic fake providers, while production paths must fail closed when configuration is absent.

### Decision 2: Centralize publish eligibility

- Choice: evaluate copy, image, approval, and account integration gates in one application-level policy before export/publish preparation.
- Rationale: prevents dashboard, export, and worker paths from using different eligibility rules.
- Alternatives: duplicate checks in each route; rejected because drift is already visible in the current data flow.
- Consequences: existing callers may need an explicit gate summary and status mapping.

### Decision 3: Align statistics to active business semantics

- Choice: define and reuse an active-product count query for the dashboard and stats API, while exposing total counts separately only if needed.
- Rationale: operators use the dashboard and API as one operational view.
- Alternatives: change only the dashboard; rejected because API consumers would remain inconsistent.
- Consequences: tests must assert both the shared active count and any explicitly named total count.

## Risks and Verification Evidence

- Provider adapters may be unavailable locally: verify fail-closed status with missing-credential and timeout tests.
- Existing migrated rows may have incomplete lifecycle metadata: run data audit and foreign-key checks before/after changes.
- Tightening export gates may reduce exported rows: verify the excluded count and reasons are reported, not silently dropped.
- External side effects must not occur during local tests: use explicit test adapters and assert no provider success is persisted without provider confirmation.
