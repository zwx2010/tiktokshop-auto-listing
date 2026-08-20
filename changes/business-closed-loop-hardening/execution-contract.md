# Execution Contract: 业务闭环完善

## Intent Lock

在现有 FastAPI + MySQL 平台上完善商品采集到上架准备的业务闭环，统一运营视图和准入门禁，并让 TikTok、AI、飞书、CDP 集成只报告真实结果；缺少凭据或运行环境时必须可诊断地失败。

## Scope Fence

### In Scope

- 看板与统计 API 的 active 商品口径统一及 MySQL 标识修正。
- 文案生成/补文案、图片质检、审批、导出和上架准备的统一门禁。
- TikTok、AI、飞书、CDP 的真实适配器状态、凭据检查、超时和失败结果。
- MySQL 读写、必要的 Alembic 迁移、回归测试和运行文档。

### Out of Scope

- 伪造 TikTok、AI、飞书、CDP 成功。
- 绕过平台风控、审批、图片质检或内容规则。
- 没有真实凭据时发布商品或把本地 fake provider 当作生产成功。
- 重写已经完成的 SQLite→MySQL 迁移基础设施，除非验证发现明确兼容性缺陷。

## Approved Behavior

1. 所有外部操作返回可区分的 `not_configured`、`unavailable`、`failed` 和已确认成功状态；非成功状态不得推进生命周期。
2. 看板和 `/api/stats` 使用同一 active-product 定义，页面不得再声称运行于 SQLite。
3. `copy_missing` 必须有可追踪的恢复路径；文案为空、违规、未验证或 provider 不可用时继续阻断导出/发布准备。
4. 发布准备集中检查文案、图片、审批和账号集成状态，并返回失败门禁原因。
5. 现有 MySQL 迁移数据、外键完整性和已验证业务接口保持可用。

## Architecture and Interface Constraints

- 保留现有分层边界：router → application service → port/adapter → persistence。
- provider adapter 不得直接把异常吞成 success；必须携带安全诊断信息，避免泄露凭据。
- 本地自动化测试使用显式 fake provider，仅用于断言适配器契约，不得进入生产默认配置。
- 任何 schema 变化必须通过 Alembic，并验证已有数据和幂等升级。
- 导出接口必须复用统一 eligibility policy，不得复制一套不一致的准入规则。

## Execution Batches

### Batch 1 — Baseline and metric consistency

Tasks: 1–2.

Deliverable: 当前数据/状态审计，统一 active 商品统计，修正 MySQL 页面说明。

Evidence: focused route/template tests; live comparison of dashboard and `/api/stats`; no data mutation beyond explicitly approved label/query changes.

### Batch 2 — Honest provider outcomes

Task: 3.

Deliverable: TikTok、AI、飞书、CDP provider outcome model, configuration checks, timeout/error mapping.

Evidence: missing-credential, unavailable-runtime, timeout, invalid-response tests; assert no success persistence.

### Batch 3 — Copy recovery

Task: 4.

Deliverable: traceable copy recovery for `copy_missing`, with provider/source/reason and content-rule validation.

Evidence: provider success, provider failure, retry, rule rejection tests; live status distribution report.

### Batch 4 — Unified publish gates

Task: 5.

Deliverable: one eligibility policy reused by export and publish-preparation paths.

Evidence: gate matrix tests for each missing/failed gate and all-pass export assertion.

### Batch 5 — Regression, diagnostics, and release proof

Tasks: 6–7.

Deliverable: MySQL/Alembic compatibility evidence, operator documentation, final smoke and audit report.

Evidence: pytest, Alembic head, health/live-ready, FK audit, export audit, no-credential integration smoke.

## Test Obligations

- Every requirement scenario in `specs/business-closed-loop.md` must have an automated test or a documented live verification command.
- Existing tests must remain green.
- No test may require real external credentials or cause real external side effects.
- Live verification may use read-only MySQL/API checks and explicit no-credential failure probes.

## Review Gates and Handoffs

- DP-3 contract approval is required before implementation.
- Each batch requires focused verification and a review receipt before dependent work begins.
- A provider, schema, export, or lifecycle behavior that diverges from the approved spec rewinds to planning/contract review before implementation continues.
- After all batches pass, run final verification and release closure; do not mark external integrations successful unless the provider confirms success.

## Escalation Rules

- If an existing adapter cannot expose truthful outcomes without changing a public contract, stop and update the spec/contract.
- If migrated rows lack fields needed for auditability, stop before destructive backfill and propose an additive migration.
- If local environment lacks credentials, implement and test the explicit non-success path; do not substitute undocumented production behavior.

## Completion Definition

The change is complete only when all approved requirements are mapped to passed evidence, the dashboard/API agree, publishable export is gate-safe, no-credential external checks fail honestly, MySQL/FK/regression checks pass, and the final verification report is recorded.
