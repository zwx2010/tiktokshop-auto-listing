# W5 upload-authorization re-review

- Base: `14bd826`
- Head: `bf3522f`
- Scope: repaired CDP-upload authorization gate.
- Verdict: **FAIL**

## Requirement

Only an affirmative, explicit command received by the robot may permit a CDP
upload.  The permission must be held in MySQL and Bitable's editable fields
must never constitute a publish authority.  A negative, conditional, quoted,
or merely referential mention of upload/publish/listing is not permission.

## What is now correct

- `UploadAuthorization` is a SQLAlchemy model with an Alembic migration, and
  runtime database configuration admits only `mysql+...` URLs.  The repository
  persists the authorization record and the Bitable worker queries it rather
  than trusting the `sel_` prefix alone.
- `approval.transition` blocks a run whose `upload_authorized` flag is false;
  the covered manual-Bitable path returns `waiting_robot_command` and does not
  invoke `_launch_upload`.
- The explicit negation examples `别上传`, `不要上传`, `不发布`, and
  `暂不上架` are rejected by the current negative-pattern check.

## Findings

### Critical — an editable Bitable batch value can still select an authorized batch

`app/agent/bitable_flow.py:_robot_upload_authorized` derives the sole database
lookup key from each row's editable `任务批次`.  It then returns true whenever
all picked rows have the same value and that value exists in
`upload_authorizations`.  The record stores only `batch_id`, command text, and
creation time; it does not bind the authorization to the server-created
Bitable record IDs or product IDs.

After a genuine robot command creates and authorizes batch `sel_X`, a user can
copy the visible `sel_X` into a manually created/edited `待上架` row.  The
poller groups every row carrying that value into the same batch, the MySQL
existence check succeeds, and the injected row reaches an approval run with
`upload_authorized=True`.  Thus MySQL is used as a lookup, but the editable
Bitable field still selects what is authorized, which violates the required
trust boundary.

Store the exact server-created Bitable record IDs (or immutable product/task
IDs) in the MySQL authorization and require the picked set to match it before
setting `upload_authorized`.  Add a regression proving that a row manually
given an existing authorized batch ID cannot upload.

### Critical — the parser accepts non-affirmative references as upload commands

`app/agent/approval.py:_is_upload_command` rejects a short list of negation
patterns but otherwise treats any occurrence of `上传`, `发布`, or `上架`
(except literal `上架表`) as affirmative.  It is not a positive-command
grammar.  For example, the current code returns true for `请问要上传吗`,
`他说上传`, and `上架前先看图`; `parse_instruction` classifies the first and
third examples as `upload`, so the resulting run receives
`upload_authorized=True` and can launch CDP after approval.

Authorize only an explicit positive imperative form, or make authorization a
separate parsed intent that defaults to false for questions, quotations,
conditions, and references.  Parameterize tests for those false cases in
addition to the repaired negation cases.

## Verification

- `git diff --check 14bd826 bf3522f` passed.
- `python -m pytest -q tests/test_upload_authorization.py
  tests/test_feishu_robot_wiring.py tests/test_workflow_integrations.py`:
  **15 passed** (one pre-existing pytest-cache warning).
- Direct predicate trace confirmed the four named negative phrases return
  false, while the three non-affirmative examples above return true.  Static
  trace confirmed the editable-batch-ID path through pickup,
  `_robot_upload_authorized`, and `approval.transition`.

The migration and initial regression coverage are useful, but the two critical
paths above still permit uploads without an affirmative robot authorization.
