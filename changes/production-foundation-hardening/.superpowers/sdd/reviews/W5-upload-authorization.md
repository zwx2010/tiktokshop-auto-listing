# W5 Upload-authorization review

- Base: `14bd826`
- Head: `ad05c71`
- Scope: robot-command authorization for CDP upload, including Bitable batch
  processing, approval transitions, and the regression tests added in
  `411ca72` / `ad05c71`.
- Verdict: **FAIL**

## Requirement

Collection, table creation, and review may be started from Bitable.  CDP
upload, however, must never be launched unless it is authorized by an
affirmative, explicit listing/upload/publish instruction received by the
robot.  An approval-card action and a Bitable row by themselves are not that
authorization.

## Findings

### Critical — Bitable data can forge the claimed robot origin

`app/agent/bitable_flow.py:91-93` treats every non-empty row whose editable
`任务批次` text starts with `sel_` as robot-authorized.  The poller accepts
pending Bitable rows (`app/feishu/bitable.py:263-290`) and preserves that field
as supplied by the row.  No immutable robot command ID, server-side issuance
record, signed token, or database lookup binds `sel_*` to an actual received
robot instruction.

Consequently a manually created/edited pending row with
`任务批次 = sel_manual` reaches `_process_group`, gets
`upload_authorized=True` (`app/agent/bitable_flow.py:406-446`), and an
`approve_all` / `approve_ok` callback reaches `_launch_upload` through
`approval.transition` (`app/agent/approval.py:149-181`).  This is a CDP upload
without the required robot-originated command.  The current regression test
actually codifies the unsafe trust boundary by asserting that a synthetic
`sel_20260826_01` field is sufficient.

The authorization must be based on a trusted, server-created record tied to
the verified inbound message and to the exact batch/rows (or an opaque,
unforgeable server-side authorization ID).  Treat Bitable fields only as
display/projection data, never as the authority to publish.

### Critical — negative wording is interpreted as permission to publish

`app/agent/approval.py:331-333` authorizes any text containing `上传`, `发布`,
or `上架` other than the exact `上架表` suffix.  It does not reject negation.
For example, `别上传` and `不要上传` match `_is_upload_command`; neither is
recognized by `_detect_mode`'s stop/review-only patterns
(`app/agent/approval.py:336-379`).  They therefore fall through to mode
`upload`, set `upload_authorized=True` (`app/agent/approval.py:913-917`), and
can launch CDP after approval.  Those messages explicitly decline upload, not
authorize it.

Require a positive direct-command grammar (and reject/route all negated,
conditional, quoted, or merely referential mentions before authorization).
Add parameterized regressions for at least `别上传`, `不要上传`, `不发布`,
`暂不上架`, and `制作上架表`.

## Verified behavior that should be retained

- A normal manual/historical Bitable row without the authorization flag is
  prepared and reviewed, but approval returns `waiting_robot_command` and does
  not call `_launch_upload`.
- `review_only` continues to return `skipped` rather than starting the upload
  stage.
- The newer direct-flow helper correctly excludes the literal phrase
  `上架表`; the issue is that it still accepts non-affirmative uses of the
  remaining words.

## Verification

- `python -m pytest -q` => **56 passed** (with two existing cache/deprecation
  warnings).
- Static trace from Bitable pickup through `_process_group` and
  `approval.transition` confirms the forged-`sel_` path above.
- Static trace from `别上传` / `不要上传` through `_detect_mode` and
  `_is_upload_command` confirms that each becomes an authorized `upload` run.

The suite proves the intended false case for `manual_01` and the literal
`上架表` exclusion, but has no test for Bitable batch-field forgery or negative
direct wording.  Passing tests therefore do not establish the required safety
property.
