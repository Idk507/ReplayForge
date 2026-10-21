# Candidate and clean-replay intent

Migration `0019` adds an immutable `replay_reservations` record per run.
`ReplayJournal.reserve` binds the complete candidate/clean plans, their operation
IDs, baseline operation, expected patched tree and unchanged regression function.
It loads accepted baseline history, checks the current VERIFYING checkpoint,
latest patch/source/test, current repository consent, approved test paths and a
live owner-scoped worker fence. The two requests must be identical except phase;
all three baseline/candidate/clean operation identities must differ.

The transaction reserves exactly two sandbox commands and the sum of their
immutable planned timeouts (currently 120 seconds), then commits the intent,
checkpoint version, budget and BUDGET_RESERVED event together. Insufficient
command/time budget closes INCONCLUSIVE/BUDGET_EXHAUSTED and the job, with no
partial debit or intent. Audit failure rolls back the whole transaction.

An identical reservation reconciles without another debit. A changed pair,
tree, original version or expired reservation cannot replace the record. Runtime
roles receive SELECT/INSERT only; updating or deleting an intent is denied.
Private reads verify canonical bytes, checksum, owner and expiry. There is no
refund/reset/retry action. An uncertain submission cannot be resolved by reserving
another pair for the same run.

`receive_replays` now requires the incoming actions to equal the private retained
reservation before processing reports. Its existing duplicate-delivery race is
also corrected: a sibling delivery may commit after the checkpoint read, so the
lease read establishes ownership/fencing while the evidence transaction owns
the final version comparison and exact idempotence. A deterministic interleaving
test covers this case in addition to concurrent deliveries. This does not waive
the committing transaction's consent, cancellation, stage or lease checks.

## Runtime boundary

These records are intent and accounting, not permission to execute. The journal
performs no provider request or workload execution. Admission of real runner
capabilities/image/configuration, remote submission, provider operation binding,
bounded polling, collector signing and fresh-compute observation remain separate
controller responsibilities. A deserialized reservation cannot authorize a
provider call or VERIFIED result. Requested-time accounting is not measured
duration, billing or a wall-clock guarantee. Preparation/apply commands require
their own budget reservations; this pair covers the two planned test commands.

Install migration 0019 and grant the trusted runtime SELECT/INSERT on
`replay_reservations` before using delivery. The local disposable test harness
now grants those minimal permissions. Existing production deployments must
apply equivalent grants with their migration identity; runtime cannot migrate.

## Observed validation — 2026-10-21

Base `76840a2` plus this increment. Focused disposable-PostgreSQL run:
**28 passed**, 960 deselected, no skips, exit 0, 45.04 seconds;
`docs/evidence/m3-replay-reservations-focused.xml`. Cases cover concurrent
idempotence/restart, exact two-command/120-second debit, both resource exhaustion
paths, wrong owner/lease/version, cancellation, audit rollback, missing/changed
reservations, private expiry/tamper, runtime update/delete denials, existing
delivery cases and the deterministic delivery interleaving regression.

The first focused run exposed the stale-read race (1 failed, 26 passed). The
final focused result above includes its fix and an explicit regression case.
The full disposable-PostgreSQL suite then passed **988 tests**, zero
failures/errors/skips, exit 0, 243.39 seconds, with one upstream Starlette/AnyIO
deprecation warning. Evidence: `docs/evidence/m3-replay-reservations-postgres.xml`.
The existing harness used a 360-second child ceiling and removed its random
test database/roles afterward; the local password was supplied only in memory.

Reproduce using the existing harness (it prompts for the administrator password):

```powershell
uv run python -c "import sys,subprocess; from scripts import test_postgres as t; call=subprocess.run; subprocess.run=lambda args,**kw:call(args,**{**kw,'timeout':360}); sys.argv=['test_postgres','--evidence-name','m3-replay-reservations-postgres.xml']; sys.exit(t.main())"
```

Ruff lint, strict mypy (91 source files), formatting of the seven changed Python
files and scoped credential-pattern checks passed. Global formatting still has
the preserved unrelated trailing space in `services/runner/smoke.py:97`.
The offline checkout checker returns M2_FIXTURE_ACCEPTED, with no execution or
verification authority. No dependency/UI changes, cloud calls, remote writes,
merge or deployment occurred. These synthetic authority tests do not establish
production runner admission or live M3 completion. M4's prior approval-policy
work remains intact; its durable journal and publisher remain future work.
