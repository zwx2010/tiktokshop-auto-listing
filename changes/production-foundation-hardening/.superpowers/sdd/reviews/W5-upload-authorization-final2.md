# W5 Upload-authorization final re-review

- Base: `bf0d93a`
- Head: `a0fef7e`
- Scope: final repair of robot-command authorization for CDP upload.
- Verdict: **PASS**

## Requirements checked

CDP upload must require an explicit affirmative robot command and approval. A
Bitable-driven run must additionally be authorized in MySQL for the exact set
of robot-created record IDs; editable Bitable fields, forged batch IDs, and
manual or mixed rows must not grant authorization.

## Review results

- `_is_upload_command` now uses an allowlisted positive grammar for `上传/发布`
  with `批次/商品/任务`, or `上架` with an allowed site/count/category scope.
  Negated forms and descriptive phrases such as `上传记录`, `发布状态`, and
  `上架情况` are rejected.
- `UploadAuthorizationRepository.is_authorized` normalizes requested and
  persisted IDs and requires exact set equality. Subsets, supersets, forged
  IDs, missing IDs, and empty requests fail closed.
- Bitable polling requires one batch and nonempty record IDs, then checks the
  MySQL authorization record. `approval.transition` refuses to call
  `_launch_upload` without `upload_authorized`, returning
  `waiting_robot_command` instead.
- Direct robot flow only sets `upload_authorized` for the strict positive
  command grammar; select-and-table flow persists authorization only for the
  actual record IDs returned by Bitable record creation.

## Verification

- `python -m pytest tests/test_feishu_robot_wiring.py tests/test_upload_authorization.py -q`:
  **5 passed**.
- `python -m pytest -q`: **57 passed** (only existing pytest cache and
  Starlette/httpx deprecation warnings).

No implementation findings remain in this review scope. No CDP upload or
external publication was performed during review.
