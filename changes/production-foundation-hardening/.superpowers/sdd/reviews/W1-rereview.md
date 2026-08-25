# W1 replacement code review

Wave: `W1` (tasks 1–2)  
Base: `12c4ab2532fc87686bab07730f0f3f7ad19796ee`  
Head: `0c14ce221ca604df16a3f825e9af4d7a4295d568`

Requirements reviewed: W1 tasks 1 and 2 in `execution-contract.md`, including the
MySQL task fact source, an independently runnable persistent worker, baseline
regressions, and the API-to-worker handoff.

## Strengths

- `scripts/worker.py` now exposes `build_worker(...)` and a runnable `main()`
  entrypoint. It constructs `PersistentTaskWorker` from the production
  `SessionLocal` and does not start implicitly inside the API process.
- The new entrypoint test verifies the production worker type, injected session
  boundary, and owner wiring. The API-to-independent-worker lifecycle test
  continues to pass.
- The optional vision-helper fallback now has explicit regression coverage: real
  source URLs are retained as unverified inputs rather than being treated as
  image-QA passes.
- Baseline Decimal pricing and task repository regressions remain covered.
- `python -m pytest -q` passes with **38 passed**. `git diff --check` is clean.
- Runtime verification in the configured environment succeeded without changing
  application code or database data: MySQL `SELECT 1` ping passed, and the
  production worker entrypoint built successfully against `SessionLocal`.
  The API→worker persistence probe also passed for the current W1 implementation;
  fresh worker instances consume the persisted task rather than an API-local
  queue.

## Issues

No Critical or Important issues remain in the reviewed W1 range.

The committed API lifecycle test uses an isolated SQLite fixture, so it is not a
substitute for the live MySQL probe. The probe evidence above covers the current
configured environment; a dedicated repeatable MySQL/restart integration test
remains desirable in W2 when lease, atomic-claim, and restart-recovery behavior
is implemented.

## Verdict

**PASS** — W1 requirements are met for the reviewed range and the prior blocking
findings (worker entrypoint and missing vision-helper regression coverage) are
resolved. W2 may begin after recording the receipt below.

## Required receipt command

```powershell
ssf execution review changes/production-foundation-hardening --wave W1 --base 12c4ab2532fc87686bab07730f0f3f7ad19796ee --head 0c14ce221ca604df16a3f825e9af4d7a4295d568 --report .superpowers/sdd/reviews/W1-rereview.md --verdict pass
```
