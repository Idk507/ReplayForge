# Bounded collection worker dispatch

services.orchestrator.worker.tick claims at most one job, resolves owner/state
from its active lease and invokes the collection graph. It returns safe IDLE or
PROCESSED metadata. There is no infinite loop, automatic retry, remote execution
or model dispatch. Operators must provide an explicitly trusted InputLoader.

serve now offers explicit bounded polling over tick: 1–1000 ticks (default 100),
a positive idle wait capped at 60 seconds (default 1), and a threading.Event-like
stop signal. Idle waits can be interrupted; processed jobs do not wait. A stop
before dispatch claims nothing. An in-flight call finishes or reaches its own
timeout before shutdown. Any worker/database/programming exception propagates
without automatic retry. Imports and constructors never start a daemon or loader.
Polling returns safe counters; it does not cancel individual runs. Deployment,
process signal wiring and supervisor/backoff policy remain operator work.

The graph calls the loader only at COLLECTING, after persisted caller/commit
consent checks. CollectionInputs keeps raw snapshot/error/secret data out of repr
and graph state. Source commit and allowed paths are checked again on loaded
input; the collector redacts before storage. Transport/time/byte/egress limits
are enforced by the configured GitHub reader when using github_inputs; arbitrary
custom loaders remain responsible for their own bounds. See source-worker.md.
No live GitHub access is established by synthetic test adapters.

Successful context storage now closes the collection job and clears its token
in the same transaction as artifact, checkpoint and TRIAGING event. This fixes
completed jobs being reclaimed and eventually exhausted. TRIAGING is a handoff,
not a verified repair; a future triage stage needs its own durable job dispatch.
Late writes through the completed claim now return LEASE_LOST rather than
STALE_VERSION. This is an intentional tightening of the claim contract.

Provider I/O failures expose INPUT_UNAVAILABLE without raw exception text and
leave the existing checkpoint for bounded lease-expiry recovery. The dispatcher
does not renew expired claims or refund attempts. Stale completion after loading
is rejected by the storage fence. Tests use disposable real PostgreSQL and
explicitly synthetic loaders, including failure and lease-expiry scenarios.

This is not a deployed background service. Scoped source loader and bounded
polling/cooperative stop exist; live credential/scope acceptance, service
configuration, supervisor/backoff and future triage scheduling remain pending.
Legacy jobs already at TRIAGING with
an active lease need explicit operator reconciliation; new completions close
atomically. M0/M1 remain partial and cloud execution stays separately authorized.

Expired claims after three acquisitions now atomically record INCONCLUSIVE with
LEASE_EXHAUSTED, advance the checkpoint version, clear the job token and emit
STAGE_CHANGED followed by LEASE_EXHAUSTED. This covers CREATED, VALIDATING,
QUEUED and COLLECTING. Repeated claims add no events. Cancellation wins when it
commits first; an audit insertion failure rolls back checkpoint and job changes.
This reconciliation occurs on the next claim call, not a timer. Later-stage jobs
and legacy TRIAGING claims still require their own reconciliation policy.

bind_incident wraps a trusted source loader with retained owner-scoped input.
It is invoked only after collection admission and stops before source I/O when
the incident is absent/expired. API-created runs now persist sanitized reports;
legacy metadata-only runs need explicit new intake, not silent backfill. See
incident-inputs.md. Synthetic loaders still do not prove live GitHub access.
