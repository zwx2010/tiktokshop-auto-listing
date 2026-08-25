# W5 Upload-authorization final review

- Base: `14bd826`
- Head: `2a89e79`
- Scope: final re-review of robot-command authorization for CDP upload.
- Verdict: **FAIL**

## Requirement

CDP upload may proceed only after a strict affirmative command received by the
robot.  For Bitable-driven work, MySQL must bind that authorization to the
exact Bitable record IDs created by the robot; editable Bitable fields cannot
add, substitute, or otherwise select publishable work.  Manual and mixed
record sets must never authorize upload.

## Findings

### Critical — record binding is subset matching, not an exact authorized batch

`UploadAuthorizationRepository.is_authorized` in
`app/infrastructure/upload_authorization_repository.py` returns true when the
current record-ID set is a nonempty *subset* of `auth.record_ids`.  Thus an
authorization persisted for `{rec_a, rec_b}` also authorizes a later picked set
of only `{rec_a}`.  The required authorization is for the exact robot-created
Bitable record set, not an arbitrary subset selected after the fact.  This
leaves the Bitable batch projection able to narrow a published batch by editing
the visible batch/status values or by changing which rows are picked.

The repair must compare normalized sets for equality (and reject duplicates or
missing IDs as appropriate), and a regression must prove that both a subset and
a superset/mixed set are denied.  The current regression covers only the
superset/mixed case.

### Critical — `_is_upload_command` still authorizes referential wording

`app/agent/approval.py:_is_upload_command` accepts any message beginning with
`上传`, `发布`, or `上架` unless its immediate next characters match a short
deny list.  It is therefore not a strict positive-command grammar.  Direct
predicate checks at this head returned true for `上传记录`, `发布状态`,
`上架情况`, and `上传不是目的`; these are references to upload/publish/listing,
not affirmative instructions to perform CDP upload.  The resulting direct
robot flow sets `upload_authorized=True` and an approval action can launch the
upload.

Use an allowlisted imperative grammar with bounded optional object/scope
syntax, rather than trying to enumerate disallowed suffixes.  Add negative
regressions for the examples above (plus quoted and conditional forms) and
verify they cannot reach `_launch_upload` after approval.

## Verified controls

- The repaired database record stores the Bitable record IDs; a manually
  injected record ID combined with an existing editable batch ID is rejected
  because the mixed set is not a subset of the stored IDs.
- A completely manual batch with no MySQL authorization is rejected.
- `approval.transition` keeps an approval run without `upload_authorized` at
  `waiting_robot_command` and does not call `_launch_upload`.
- `0006_upload_authorizations` followed by
  `0007_upload_auth_records` forms a valid Alembic chain; `alembic heads`
  reports `0007_upload_auth_records (head)`.  Existing authorization rows with
  null `record_ids` fail closed.

## Verification

- `git diff --check 14bd826 2a89e79` passed.
- `python -m pytest -q tests/test_upload_authorization.py
  tests/test_feishu_robot_wiring.py tests/test_workflow_integrations.py`:
  **15 passed** (one pre-existing pytest-cache warning).
- In-memory repository trace: exact set passed; mixed/manual set failed; a
  strict subset incorrectly passed.
- Direct command-predicate trace found the referential false positives listed
  above.

The MySQL migration and mixed-manual binding are materially improved, but the
subset authorization and non-command wording defects still permit upload
without the required exact, strict robot authorization.
