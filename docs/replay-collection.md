# Durable known-replay observation collection

## Implemented behavior

`services.orchestrator.replay_collection.collect_replay` connects a durable
candidate/clean-replay submission to one bounded provider read and optional JUnit
download. The operation UUID comes only from replay_submission_results; neither
browser/model input nor a guessed UUID can select an operation through this
controller. A sticky claim without a known result fails before HTTP. No POST,
DELETE, resubmission, polling loop, cancellation, signing or verdict occurs.

Before I/O it resolves the unexpired owner-scoped replay reservation, current
worker lease/checkpoint/version, source/test/patch bindings, consent and allowed
test paths. Plan hash and adapter origin must match the recorded submission.
The source image UUID remains trusted operator configuration; its relationship
to the plan's OCI digest still requires independent assessment.

The reader uses the existing fixed-host REST client with five-second request
bounds, response-size limits, identity encoding, no redirects/proxy discovery and
sanitized exceptions. It performs at most one operation GET and one report GET.
Operation and source-image UUIDs must match. Status reads do not request inflight
output. SUCCESS/FAILED require a valid strict process-state object; SUCCESS is
not interpreted as a zero guest exit. stdout/stderr and arbitrary provider
metadata are discarded from the observation.

Only PYTEST_FILES_SAVED_JUNIT_V1 permits report collection. A distinct result-image
UUID supplies the fixed /tmp/replayforge-result.xml path. Reports are bounded to
65536 bytes, encoded privately as base64 and bound to their actual SHA256. Empty,
oversized, redirected, malformed or incorrectly correlated responses fail
explicitly without retaining success. XML is not executed, interpreted as a
command or accepted as verified by this adapter.

## Durable outcomes and races

ReplayObservation is a closed versioned object with trace, observation time,
policy decision, origin, plan/capability/report refs, process facts and explicit
false execution/verification authority:

- PENDING: current nonterminal operation. No private terminal record or event is
  appended, so a later explicit call can observe progress.
- REPORT_COLLECTED: terminal operation with actual private report bytes. A failed
  test can legitimately produce this status; it does not mean test success.
- TERMINAL_NO_REPORT: terminal cancellation or completed operation without a
  saved image/report. Missing evidence is preserved as missing, never invented.

After I/O the controller repeats lease/version/source/test/patch/consent checks.
Terminal observations and a metadata-only REPLAY_OBSERVATION_RETAINED event commit
in one transaction, leaving run stage, budget, checkpoint and job unchanged.
Migration 0021 adds the private append-only replay_observations table. The runtime
role needs SELECT/INSERT only; deployment grants remain an explicit operator task.
The record expires with the original replay reservation instead of renewing its
retention. Expiry is not physical purge. No API endpoint exposes these raw bytes.

A restart returns a checksum/schema/correlation-checked terminal record without
HTTP or submission. Concurrent identical terminal reads reconcile to one record
and event, ignoring only their observation timestamps. A terminal record that
commits while another read is pending wins over the stale pending observation.
Conflicting terminal facts fail closed. Audit failures roll back private retention.
Malformed/expired cached records are not silently refetched to hide corruption.

Raw JUnit is private untrusted evidence and may contain sensitive guest text.
Do not put it into public events, model memory, telemetry or UI without the
existing authorized/redaction boundaries. Only process-state fields are retained
from provider metadata. The observation's hash is integrity, not authenticity.
No collector signing key, workspace measurement, capability assessment or VERIFIED
producer is installed. Fresh independent source/final-tree/test measurements and
a trusted receipt producer remain the next integration requirement. Saved result
filesystems/images and logs are not deleted by closing the HTTP client.

## Provider references checked 2026-10-22

The human-readable operation-status reference was unavailable through the web
reader. The official documentation index links the
[Contree OpenAPI specification](https://eu-north.nebius.computer/static/api.yaml),
which was fetched without credentials. It defines GET /v1/operations/{operationId}
and optional inflight reads, and the fixed image download endpoint used here.
The implementation also follows the existing smoke client's metadata.result.state
shape and bounded transport. No new live provider observation was made. See
[prepared backend notes](prepared-nebius-backend.md) for persistence and image
identity limits.

## Reproducible checks

```powershell
uv run pytest tests/contract/test_observation_reader.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Use the isolated PostgreSQL harness and 420-second outer suite override documented
in memory.md for the full suite. Individual test, lease and product timeouts are
unchanged. Database tests use tiny inert report/source fixtures and synthetic HTTP;
no repository code executes on the Windows/API host.


## 2026-10-22 — Durable known-replay report collection

Implementation commit **c949e1e**, base d009dd3. Added the bounded read-only
observation reader, owner/lease/version/source/test/patch/consent fenced replay
collector and migration 0021 private terminal observation journal. Known IDs come
only from durable submission results. Only GETs are allowed; unknown submissions
are never guessed or repeated. Strict process state and exact bounded JUnit bytes
are retained independently of provider SUCCESS. Private report SHA256/canonical
record bindings, immutable runtime role and reservation-derived expiry apply.

Pending reads are not retained as terminal. Restart reconciles without HTTP.
A reproduced pending-vs-terminal race was fixed: validated durable terminal facts
win over an older pending read. Concurrent terminal reads produce one record/event;
conflicting terminal facts fail. Cancellation/lease/version changes during I/O
reject retention, and an audit failure rolls the private insert back. No checkpoint,
stage, budget or job is changed by observation retention.

Observed checks (final exit 0):
- `uv run pytest tests/contract/test_observation_reader.py -q --junitxml=docs/evidence/m3-observation-reader.xml`: **13 passed**, zero skips.
- Focused disposable-PostgreSQL collection run: **15 passed**, 1087 deselected,
  zero skips, 34.80s (`m3-replay-collection-focused.xml`).
- Additional atomic rollback/concurrent-read run: **2 passed**, 1102 deselected,
  zero skips, 7.89s (`m3-replay-collection-atomic.xml`).
- Full disposable-PostgreSQL regression with the existing 420-second outer
  harness override: **1104 passed**, zero failures/errors/skips, **373.37s console**,
  one existing upstream Starlette/AnyIO warning. JUnit suite time: 373.324s.
  Report: m3-replay-collection-postgres.xml, SHA256 `0d8bc9ccac92099d6c20b77ca6e967269f74a0bf31069c605a5beee5f7ebbaa8`.
- Global Ruff lint/format passed (199 files); strict mypy passed (99 source files).
  Scoped credential-value scan and git diff checks passed. The offline retained
  checkout checker still returned M2_FIXTURE_ACCEPTED.

Initial missing-module RED and pending/terminal race RED are retained losslessly
as m3-replay-collection-red.xml.gz and m3-replay-collection-race-red.xml.gz. No test
was weakened, skipped or suppressed. Task-owned disposable DB/roles were cleaned.
No dependency or UI changes required repeating their previous audits/browser runs.

Provider-status HTML was unavailable; the official index-linked OpenAPI spec was
read without credentials, alongside existing smoke response contracts. No live
inference/sandbox/Tavily request, remote repository write, merge or deployment
occurred. Actual report provenance in tests is synthetic HTTP and ephemeral test
capabilities, never proof of a production admitted runner. Auth remains unchanged.

M3 remains NOT CLOSED. Observations are unsigned and cannot authorize verification;
source/final-tree identity, OCI/image/configuration assessment, trusted receipt
production and live seven-gate acceptance remain required. The combined harness
still reaches REPRODUCING; this collector is an explicit separate internal action.
Next: independent workspace measurements and trusted receipt production, with
admitted baseline/candidate/clean execution and authentic UI evidence. Actual
assessed deployment facts/keys must not be invented or replaced with test keys.
Details and retention/privacy limits: docs/replay-collection.md.
