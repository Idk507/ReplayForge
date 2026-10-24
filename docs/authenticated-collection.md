# Authenticated collection worker

The loopback auth service owns GitHub credentials. A separate worker claims
durable collection jobs and submits the lease, expected version and selected
paths. It receives status metadata, never provider tokens or repository content.
This slice performs no inference, repository execution or publication.

## Operator startup

Use separate restricted auth, API and worker database roles. Explicitly enable
`REPLAYFORGE_ENABLE_COLLECTION_BROKER=true` alongside the existing authorized
GitHub read flag. Share a fresh URL-safe 43-character
`REPLAYFORGE_COLLECTION_WORKER_KEY` with the worker. Keep the API on
127.0.0.1:8000 without access logs.

The ignored `.env.worker` accepts exactly four keys: `REPLAYFORGE_DATABASE_URL`
(a URL DSN for the restricted worker), `REPLAYFORGE_COLLECTION_WORKER_KEY`,
`REPLAYFORGE_COLLECTION_BROKER_URL=http://127.0.0.1:8000`, and
`REPLAYFORGE_WORKER_PATHS=["pyproject.toml"]`.

Worker privileges: schema USAGE; runs/job_leases SELECT and UPDATE; run_events
SELECT and INSERT. No auth credential access, grant INSERT, owner-role membership
or administrative privileges. The API operation role also needs INSERT on
context_artifacts/source_manifests for the existing atomic reducer.

```powershell
uv run --no-env-file python -m scripts.launch_authenticated_worker --run --env-file .env.worker --max-ticks 1
```

Without `--run`, startup returns NOT_RUN/exit 2 without configuration/network I/O.
The launcher strips inherited provider/vault credentials before starting the
worker. Never load the operator `.env` into the worker. Input is limited to 8 KiB
and four keys. Its child has a 300-second outer limit; this local launcher is not
a production supervisor. Reconcile durable state after an unconfirmed timeout.

## Contract

GET `/internal/v1/collection/ready` and POST `/internal/v1/collection` require an
independent workload bearer key and reject browser Origin/fetch headers. They are
outside the BFF vocabulary. A lease alone is not authentication. POST requires
a current lease, exact version, collection stage and fresh membership/consent;
the auth service checks again before source I/O. Unique selected paths (1-16)
must be safe and within the original grant. Reads preserve registered roots,
verify pinned identities and reject missing files. No host shell or root
expansion is enabled.

The reducer commits context, source identity, events and triage queue atomically.
The client correlates persisted source/repository/stage after the response.
Unknown results are not blindly retried. Replies are bounded to 4 KiB, with
fixed loopback URLs, explicit timeouts, no proxy inheritance or redirects.
Upstream collection is read-only, separate from model/sandbox submission.

Oversized files remain omitted with CONTEXT_BUDGET, never truncated to manufacture
context. README by default does not guarantee useful diagnosis. Select a small,
relevant approved file for an actual incident.

## Observed acceptance: 2026-10-24

Real-Postgres synthetic tests cover intake/dispatch/checkpoint, invalid workload
authentication, stale fences, cancellation, revoked consent and unsafe paths.
Client tests cover uncertain timeouts without retries. Source tests cover nested
selection without weakening roots.

Live read-only acceptance used user-approved public Idk507/maf_ds_agent at
08d828835fcb0cada16aca45c721ad0075e0c70e. README (15,629 bytes) was omitted by
the context budget and recorded as metadata-only. A fresh run selected
pyproject.toml (2,117 bytes), retained one content file/source manifest and reached
TRIAGING. See `evidence/collection-worker-small-acceptance.json`. The earlier user
sample had none of the approved root paths and ended INCONCLUSIVE; that result
is preserved. No terminal run was reset. No inference, sandbox or external write
occurred. GitHub HTTPS source metadata is not a signed execution receipt.

Remaining: incident-driven path selection, triage/repair worker integration,
authentic seven-gate replay, session-bound approval/token brokerage, approved
live PR and hidden evaluation. M3/M4 acceptance remains unchanged.
