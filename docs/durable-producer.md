# Durable measured execution recovery

`DurableMeasuredBackend` connects the measured runner to `ProducerJournal`.
Supply it through the existing `dispatch_replay` controller. Construction has no
provider/database side effects and requires matching prepared binding, receipt
key ID/public key and transport origin. No production identity is discovered.

The source workspace is independently measured after admission. Its exact plan,
prepared binding, measured identity and database observation time commit to
`replay_preparations` with an audit event **before POST**. Preparation/audit failure
prevents submission. A saved preparation cannot authorize a second POST, including
when the original submission outcome is unknown. No budget is refunded.

After restart, collection resolves only an operation already bound in the durable
submission journal. It restores the exact source measurement; expected hashes
cannot substitute for missing observations. It measures the terminal result and
signs actual report/process/tree facts using the existing producer, then commits
`replay_produced_receipts` plus its audit event before returning. Failed retention
can retry its identical in-memory receipt without repeating HTTP. Concurrent
collectors reconcile immutable facts and return the first retained receipt/time.

Cached reads revalidate canonical bytes, SHA256, signature and exact
run/phase/execution/operation/source/image/configuration/key/origin bindings.
Owner, live lease, version, current source/test/patch and repository consent are
checked on every action. Expiry is inherited from the replay reservation;
preparation and receipt retention never renew it. Raw JUnit is bounded private
base64 in the carrier; no report or signing secret is written to events.

`deliver_produced_replays` reads the two completed producer journals, then calls
the existing signed replay/evidence reducer. It accepts no caller-selected
operation ID or raw report. It supports exact duplicate delivery at the next run
version, preserves the retained bundle and never advances to VERIFIED. Actual
JUnit/signatures still pass the existing independent pair verifier. TEST_TRANSPORT
reports remain denied for production evidence. Source/image assessment remains
trusted operator/materializer input, not a fact inferred from a UUID.

## Database and deployment

Migration 0022 adds the two append-only tables. The integration harness grants
SELECT/INSERT only. Deployment must provision equally restricted worker grants;
no production migration or grants were applied. There is no purge/deletion claim.
Context exit still closes HTTP resources, not remote compute. Unknown operation
IDs cannot be guessed/attached and never trigger resubmission. These features do
not enable the default harness past REPRODUCING automatically.

## Observed focused tests

- `m3-durable-producer-focused.xml`: 17 passed, zero skips, 39.27s, exit 0.
  Covers fresh backend restoration, cached no-HTTP reads, current fences,
  corruption/expiry/consent, unknown IDs, audit rollback, concurrent collection,
  signature/content tampering and runtime UPDATE/DELETE denial.
- `m3-produced-delivery-focused.xml`: 4 passed, 1183 deselected, 15.76s, exit 0.
  Uses explicitly synthetic remote-shaped signatures/internal fake dispatch
  facts to exercise exact handoff/idempotence and wrong context rejection.
  Those tests are not a live provider repair or seven-gate acceptance.
- Initial absent-module RED is retained losslessly as
  `m3-durable-producer-red.xml.gz`.

Remaining M3 work: baseline/general materialization, configured production
signer/OCI/configuration assessment, deterministic seven-gate production, live
acceptance and the complete admitted worker-to-UI journey. Existing M2 fixture
acceptance is preserved; no security gate or milestone exit was weakened.


## Full acceptance evidence — implementation 9df1ac9

`m3-m4-durable-workflow-postgres.xml`: 1191 passed, zero failures/errors/skips,
449.25 seconds console / 449.212 seconds JUnit, exit 0. SHA256:
d709171cdedbf5725f1446d330db593c0ff9f37d9dba604021d241e6282a9c2e.
Ruff check/format pass (213 files); strict mypy passes (105 source files).
The retained M2 checker still returns M2_FIXTURE_ACCEPTED. One upstream
Starlette/AnyIO deprecation warning remains. No UI/dependency changes required
repeating their unchanged browser/dependency audits. No live provider ran.

Reproduce full regression (prompts for local PostgreSQL administrator password):

```powershell
uv run python -c "import sys,subprocess; from scripts import test_postgres as t; call=subprocess.run; subprocess.run=lambda args,**kw:call(args,**{**kw,'timeout':600}); sys.argv=['test_postgres','--evidence-name','m3-m4-durable-workflow-postgres.xml']; sys.exit(t.main())"
```

The 600-second ceiling applies only to the aggregate test process, whose actual
suite time is recorded above. No individual test timeout, product budget,
security requirement or verifier gate was weakened. Disposable DB/roles cleaned.
An earlier full run was intentionally terminated before completion to fix the
reproduced approval/memory integration bug; it supplied no acceptance result.
