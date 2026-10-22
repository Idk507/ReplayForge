# One-shot admitted replay dispatch

`services.orchestrator.replay_dispatch.dispatch_replay` connects the retained
candidate/clean reservation to an explicitly injected prepared-workspace backend.
It performs no adapter discovery, credential loading, shell execution or public
HTTP routing. Migration `0020` adds append-only submission claims and provider
operation bindings, plus the two allowlisted audit event names. Runtime needs
SELECT/INSERT on both new tables; it cannot update/delete submission history.

Before creating a claim, the controller checks the owner-scoped live worker
fence, exact checkpoint version, VERIFYING stage, cancellation, current consent,
approved test paths and reserved source/manifest/test/latest patch identities.
It authenticates capability evidence with the existing configured verifier and
database time, and checks that the backend runner/configuration identities match.
Missing/invalid capabilities prevent the backend call. A remote backend also
requires explicit `allow_live=True`; this flag cannot waive admission.

The claim and REPLAY_SUBMISSION_CLAIMED event commit **before** `submit` is called.
Its primary key allows only one attempt per run and phase, including across
workers/restarts. Another caller observes SUBMISSION_UNKNOWN while submission
is unresolved, or ALREADY_SUBMITTED with the retained provider operation.
It never invokes the backend again. Timeout/connection loss leaves the claim
intact, returns nonretryable SUBMISSION_UNKNOWN, and cannot refund budgets.
Other backend errors propagate with the claim intact; adapters must return safe
bounded errors and must not embed credentials in exception messages.

After I/O, live lease and exact checkpoint are checked again. The result and
REPLAY_OPERATION_BOUND event commit together. Provider UUIDs are globally unique
in this table, so the same operation cannot stand for both phases or two runs.
Cancellation/lease loss or result-audit failure prevents result acceptance while
leaving the attempt sticky. A crash after the provider accepts a request but
before local binding may therefore leave an unrecoverable unknown submission;
there is deliberately no blind retry. Cancellation immediately before submission
cannot be atomic with a remote provider call. The adapter must enforce bounded
remote lifetime/owned cleanup independently; a local claim is not cleanup proof.

Results include plan reference, command trace, observation time, policy decision,
origin and safe status. Neither provider operation identity nor a SUBMITTED
status proves that tests passed. `can_authorize_verification` is always false.
Job completion, polling, signed report delivery and seven-gate acceptance are
separate steps. This controller does not spend budget again: migration 0019
already reserved the two test commands and their requested duration.

## Prepared backend contract and deployment boundary

`PreparedTestBackend` is a trusted operator dependency, not a model-selected
tool. It must bind the actual assessed runner/image/configuration, materialize
the pinned source, admitted patch and unchanged regression in fresh remote
compute, validate the expected patched tree, and execute only the supplied
application-owned plan. Its submit method must have a bounded request deadline;
the control plane must not hold a database transaction during network I/O.

No production implementation is installed by this increment. The existing M0
REST client remains fixed-smoke-only and is not silently repurposed for repository
execution. Implementing this port with host subprocess execution, arbitrary
shell strings or a provider UUID treated as verified OCI identity would violate
the contract. A successful capability signature authenticates its configured
assessor; it does not invent the underlying assessed facts or workspace binding.

Tests use a small fake backend and ephemeral capability signer, with explicit
TEST_TRANSPORT outputs. REMOTE_SERVICE-shaped capability fixtures exercise the
admission contract only; they are not production assessment. No cloud request
or real repository workload is needed for these tests. Live M3 remains open until
the actual prepared-workspace backend, assessor/collector configuration, bounded
polling/cleanup and authentic seven-gate acceptance are available and exercised.

## Observed validation

Base `399a424` plus this increment. The retained focused report from the
interrupted 2026-10-21 session records **21 passed**, no failures/errors/skips,
36.94 seconds (`docs/evidence/m3-replay-dispatch-focused.xml`). Initial testing
had found missing database event allowlist entries; migration 0020 now adds
them without removing the existing vocabulary. No tests were disabled to pass.

Current-tree full validation was completed for the 2026-10-22 continuation:
**1009 passed**, zero failures/errors/skips, exit 0, 315.38 seconds, with one
upstream Starlette/AnyIO deprecation warning. Evidence:
`docs/evidence/m3-replay-dispatch-postgres.xml`. The existing harness used a
random disposable local database and restricted runtime role, removed on exit.
The local database password remained in memory, not in source or artifacts.

Reproduction command (prompts for the local administrator password):

```powershell
uv run python -c "import sys,subprocess; from scripts import test_postgres as t; call=subprocess.run; subprocess.run=lambda args,**kw:call(args,**{**kw,'timeout':360}); sys.argv=['test_postgres','--evidence-name','m3-replay-dispatch-postgres.xml']; sys.exit(t.main())"
```

Cases cover both phases, restart/concurrency, timeout/connection loss, owner/
lease/version/cancellation/consent denials, forged/missing capability controls,
backend runner/configuration mismatch, missing live authorization, claim/result
audit rollback, stale worker after I/O, duplicate provider operation IDs and
runtime deletion denial. Fake calls never run repository code.

Ruff lint, strict mypy (92 source files), changed-file formatting/diff checks and
scoped credential-pattern scans passed. The retained M2 checker still returns
M2_FIXTURE_ACCEPTED with no execution/verification authority. Global formatting
still includes the preserved unrelated trailing space in smoke.py:97. Dependencies
and UI were unchanged, so their audits/browser checks were not repeated. No cloud
call, remote repository write, merge or deployment occurred. Production readiness
and live M3 completion are not claimed; M4's prior approval kernel is unchanged.
