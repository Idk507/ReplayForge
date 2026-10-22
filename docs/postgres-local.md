# Local PostgreSQL persistence and API

The user authorized local development while sandbox activation is pending.
This implementation uses real PostgreSQL, not SQLite or mocked transactions.
It does not execute repositories or make cloud calls.

## Reproduce the database tests on Windows

Start the installed PostgreSQL service on 127.0.0.1:5432, then run:

```powershell
uv sync --locked
uv run python -m scripts.test_postgres
```

Enter the local postgres administrator password at the hidden prompt. The helper
creates randomly named task-owned test database and roles, runs the entire suite,
then drops those resources even when tests fail. Passwords are not placed in
command arguments, printed, or saved in repository files. Migration and runtime
roles are separate, non-superuser roles. The runtime role cannot perform DDL or
update/delete event rows. A permanent development database is not created by this
helper. Do not point integration tests at existing databases: the downgrade test
and fault-injection tests deliberately modify the test schema.

`uv run pytest -q` without the helper skips real database integration scenarios
unless their dedicated test environment is configured. A skip is not acceptance.

## Storage contracts

Store is a trusted internal control-plane adapter, never a model tool. Server
authentication must supply owner UUIDs. Run creation commits checkpoint, initial
event and queued job in one transaction. Unique owner/idempotency keys plus a
transaction-scoped advisory lock serialize duplicate requests. Payload digest
and immutable identity conflicts are rejected; generated run IDs may differ on
retries. Checkpoints contain artifact references and counters, never raw logs.

Stage/cancel/reservation updates lock rows, check expected versions and append
events in the same transaction. Event pages are ordered, bounded to 100 records,
and owner scoped. Events double as a durable outbox; there is no separate delivery
consumer or delivery acknowledgement ledger yet.

Claims lock both runs and jobs with SKIP LOCKED. Tokens and server-clock expiry
fence stale workers. Heartbeats renew live claims only. There are at most three
claims per job. Expired third claims become exhausted with an audit event and
require explicit reconciliation; this is not yet automatic run failure routing.
Action debits survive restarts, and failed provider work must not refund them.
Exactly-once external effects, signed runner evidence, cost/time reservations,
LangGraph checkpoints and full domain tables remain future work.

## Local API boundary

`services.api.app.create_app(store, auth)` is an application factory tested with
real PostgreSQL. LocalAuth requires a server-configured token of 32–256 printable
ASCII characters, operator UUID and approved repository UUIDs. No configuration
means admission fails closed. Use a randomly generated token, not a password.
Bind any development server to 127.0.0.1 and disable proxy-header interpretation.
Do not put this API behind a public proxy or use it as production authentication.

Implemented routes: POST /api/v1/runs (202), GET /api/v1/runs/{id}, POST
/api/v1/runs/{id}/cancel with expected_version, and GET /api/v1/runs/{id}/events.
Events are finite SSE replay pages; reconnect with Last-Event-ID for unseen
records. No live heartbeat events are invented. A completion comment terminates
each page. Authenticated but unapproved repositories return 403; another owner's
run is concealed as 404. Invalid input responses never echo incident bodies.
HTTP input is capped at 32 KiB before validation; no cookies or CORS grants exist.

Intake hashes the request and stores a bounded redacted incident report atomically
with the run (migration 0004). This proves durable intake, not an operational
incident investigation. Worker input binding, repository URL/consent resolution,
rate limits and production
OIDC authentication must precede public or repair-workflow use. There is no API
route for verifier writes, approval or PR creation. Cancellation fences local
jobs but cannot terminate a future sandbox until that integration is built.

Migrations read REPLAYFORGE_MIGRATION_DATABASE_URL (or DATABASE_URL) from the
environment; credentials are absent from alembic.ini. Use a separate migrator
role and grant runtime SELECT/INSERT/UPDATE on runs/job_leases, SELECT/INSERT on
run_events and schema USAGE. Schema rollback is destructive and only tested in
disposable databases. The source checkout contains migrations; the wheel alone
does not package an operator migration environment.

Migration 0002 adds repository_grants. Operator/migration role inserts trusted
consent records; runtime needs SELECT only. API intake requires a persisted
caller/commit grant in addition to the LocalAuth repository UUID list.

Migration 0003 adds context_artifacts; runtime needs SELECT/INSERT only. Context
storage atomically advances COLLECTING to TRIAGING with a hashed reference.
Artifact reads enforce owner, expiry and checksum checks. Expiry is not deletion;
see context-artifacts.md.

Migration 0004 adds incident_reports; runtime needs SELECT/INSERT only, without
UPDATE/DELETE. Legacy runs have no retained report. See incident-inputs.md.

Migration 0005 adds source_manifests; runtime needs SELECT/INSERT only, without
UPDATE/DELETE. Observed GitHub collection now commits source metadata alongside
context and checkpoint. See source-manifests.md; raw source bytes are not stored.

For separately named test evidence, run
`uv run python -m scripts.test_postgres --evidence-name m1-worker-tests.xml`.
The harness accepts only a simple lowercase XML filename under docs/evidence
and rejects traversal before requesting database credentials.


Migration 0021 adds private replay_observations, linked to a known replay
submission. The worker role needs SELECT/INSERT only, with no UPDATE/DELETE.
The disposable test harness provisions those test permissions; production/local
persistent deployment grants remain an operator task. The controller appends a
metadata-only REPLAY_OBSERVATION_RETAINED audit event and leaves checkpoints and
jobs unchanged. See replay-collection.md for owner/lease/expiry/read boundaries.


Migrations 0022/0023 add replay_preparations, replay_produced_receipts,
approval_decisions and publication_intents. The disposable integration harness
grants runtime SELECT/INSERT only on these tables. Production must provision
restricted worker and human-approval identities separately; no production grants
or schema were applied. See durable-producer.md and approval-journal.md.
