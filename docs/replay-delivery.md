# Durable candidate and replay delivery

`services.orchestrator.replay_delivery.receive_replays` is an internal worker
entry point, with no HTTP route, provider request or model tool. It receives
collector-signed reports and trusted planned actions. It checks the current
owner-scoped VERIFYING checkpoint, source/repository, source manifest, latest
patch, unchanged reproduction and accepted baseline identity before retaining
evidence. The baseline's stored report is checksum-checked again; the exact
regression function/path must match the retained baseline action.

Candidate and clean reports are authenticated with the existing pair validator
using PostgreSQL time. TEST_TRANSPORT evidence is denied by the delivery path.
Tests use explicitly synthetic signing authority with REMOTE_SERVICE-shaped
fixtures; that does not demonstrate a real collector.

The exact plans, signed receipts and JUnit bytes become five private artifacts
in the existing evidence bundle. Additional trusted producer artifacts can be
included, subject to name uniqueness and the existing 32-file/64-KiB-per-file
limits. This retention limit is deliberately smaller than the pure receipt
inspector's 128-KiB report limit. All files must be sanitized before signing;
redacting a signed report afterward invalidates its digest.

The existing evidence transaction rechecks current consent, owner, live lease,
stage and version before committing bundle/checkpoint/event together. Concurrent
identical delivery commits once. Restart with the same original version and
exact bundle returns the retained checkpoint as a private historical read.
Conflicting or expired evidence cannot replace it. There is no budget refund,
provider retry or automatic inference. A failed authentic report is retained
without declaring the run VERIFIED; subsequent deterministic gates decide the
outcome. Delivery never changes the VERIFYING stage or completes the job.

## Limits and next integration

Planned actions are trusted inputs, not public/model-submitted data. This
receiver does not implement candidate dispatch reservations, remote execution,
operation polling or queue scheduling. It cannot establish fresh compute,
assessed image/configuration facts or a production collector key. The caller
must load those expectations from its admitted execution controller. Additional
mandatory gates and actual patch/test artifact bytes are required for the final
authenticated manifest; these five delivery artifacts alone cannot satisfy it.

This increment closes durable receipt delivery and exact restart reconciliation.
M3 live acceptance remains open until the collector/runner and full gate
producer exist and are exercised. M4's approval policy can be developed offline
without relaxing this boundary or performing GitHub writes.

## Observed acceptance — 2026-10-21

Base `3315bca` plus this increment. The existing disposable PostgreSQL harness
ran 13 focused cases (940 deselected), exit 0, zero skips, 21.31 seconds:
`docs/evidence/m3-replay-delivery-focused.xml`. It exercised successful and
authentic failed report delivery, concurrent/idempotent restart, wrong owner,
lost lease, stale version, changed patch/test, missing baseline, forged signature,
cancellation, revoked consent, audit rollback and conflicting bundles.

The final full harness run, with a 300-second child ceiling, passed **973 tests**,
zero failures/errors/skips, exit 0, 217.31 seconds. Machine-readable evidence:
`docs/evidence/m3-delivery-m4-approval-postgres.xml`. One upstream
Starlette/AnyIO deprecation warning remains. The harness created and removed
only random task-owned databases/roles; its administrator password remained in
memory and was not saved in artifacts.

Reproduce the full run with the existing harness and a larger child ceiling
(the command prompts for the local database administrator password):

```powershell
uv run python -c "import sys,subprocess; from scripts import test_postgres as t; call=subprocess.run; subprocess.run=lambda args,**kw:call(args,**{**kw,'timeout':300}); sys.argv=['test_postgres','--evidence-name','m3-delivery-m4-approval-postgres.xml']; sys.exit(t.main())"
```

Ruff lint and strict mypy (90 source files) passed. Changed-file formatting,
whitespace and scoped credential-pattern checks passed. Global formatting has
the pre-existing user edit adding a trailing space to `services/runner/smoke.py:97`,
preserved outside this commit. No dependency/UI edits, cloud calls, remote
repository writes, merge or deployment occurred. The retained offline M2
checkout checker still returns M2_FIXTURE_ACCEPTED without execution authority.
