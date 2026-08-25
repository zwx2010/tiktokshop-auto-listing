# W4 Feishu runtime re-review

- Base: `d2be662`
- Head: `0c00109`
- Scope: Feishu robot runtime wiring against the approved control-plane contract.
- Verdict: **PASS**

## Contract checks

1. **Verified Feishu message reaches the approval orchestrator — PASS.**
   `app.main` registers `approval.on_message` and `approval.on_card_action` in
   `handlers` at import time.  The signed `/api/feishu/webhook` route verifies
   the request before dispatching `im.message.receive_v1`; dispatch then calls
   the registered handler, whose message path parses the text and invokes
   `approval.handle_instruction`.  The card route follows the same verified
   handler wiring for approval decisions.

2. **Bitable polling is opt-in — PASS.**
   The application lifespan runs `_configure_robot_runtime`, which starts the
   poller only when `BITABLE_POLL_ENABLED` is exactly `1` after trimming.  With
   the variable absent or any other value, it only logs that polling is disabled.
   A second startup call is guarded by `_poll_started`; missing Bitable
   configuration also prevents startup.

3. **Enabled poller only claims pending listing rows — PASS.**
   `_poll_loop` calls `bitable.pickup_pending("待上架", "处理中")` and does
   not enumerate any other status.  `pickup_pending` searches the listing table
   with `状态 == 待上架`, then immediately writes only those returned rows to
   `处理中` with a lock time.  Batch filtering may restrict this set to the
   newest pending batch, but cannot add non-pending rows.

4. **No implicit publication — PASS.**
   Polling can prepare a batch and send an approval card, but publication still
   occurs only after a distinct `approve_all` or `approve_ok` card action is
   accepted by the approval state machine.  Rejection and `review_only` do not
   launch the upload stage.

## Verification

Executed with `BITABLE_POLL_ENABLED` unset:

```text
pytest -q tests/test_feishu_robot_wiring.py tests/test_api_lifecycle.py
9 passed, 2 warnings in 0.68s
git show --check 0c00109
git diff --check d2be662 0c00109
```

The new tests prove runtime handler registration and forwarding into
`approval.handle_instruction`; the lifecycle test proves explicit initializer
execution.  The disabled/enabled poll branches and the signed HTTP callback
are established by direct code-path inspection in this review, rather than
dedicated endpoint/branch tests.  This is a useful future coverage addition,
but no contract violation was found in the reviewed change.

## Note outside this commit

`app/routers/feishu.py` still logs truncated raw webhook/card payloads.  That
predates `0c00109`; it is outside this focused runtime-wiring diff and does not
alter this PASS verdict, but remains relevant to the broader execution-contract
requirement that raw callbacks are not logged.
