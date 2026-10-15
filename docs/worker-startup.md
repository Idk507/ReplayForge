# Local collection worker entry point

`uv run python -m scripts.run_worker` returns NOT_RUN (exit 2) without reading
configuration, retrieving credentials, opening a DB connection or starting a
loader. Import also starts nothing. This is the default development behavior.
The entry function returns 2; the observed Windows command wrapper normalized
that nonzero exit to 1. NOT_RUN is expected, not a successful worker dispatch.

Explicit startup is `uv run python -m scripts.run_worker --run`. It performs at
most one collection tick by default. `--max-ticks N` permits 1–1000 ticks; idle
waits and cooperative shutdown use the existing bounded serve loop. This command
can read GitHub when an admitted collection job exists. It does not call models,
Tavily or a sandbox, execute repository code, create grants or run migrations.

Inject operator configuration through the process environment using your local
secret provider; never put real values in commands, checked-in files or logs:

- REPLAYFORGE_DATABASE_URL: password-bearing PostgreSQL connection string for
  the restricted runtime role, explicit database/user and loopback host.
- REPLAYFORGE_GITHUB_READ_TOKEN: separately scoped GitHub read credential.
- REPLAYFORGE_WORKER_PATHS: JSON array of 1–16 unique exact paths, for example
  `["src/example.py", "requirements.txt"]`. Persisted per-repository consent is
  rechecked at dispatch; configuration alone cannot grant access.

Only localhost, 127.0.0.1 and ::1 DB hosts are accepted in this local-only slice.
Service definitions, caller-supplied hostaddr/options and unknown libpq options
are rejected. Explicit hostaddr defeats PGHOSTADDR redirection. Connection timeout
is fixed at five seconds, with bounded SQL/lock timeouts. Startup rejects
superuser, database/role creator and repository-grant writer identities before
constructing the source loader. This is a startup guard, not a full grant audit;
apply the documented runtime table privileges from postgres-local.md.

SIGINT/SIGTERM request cooperative stop. In-flight collection finishes or reaches
its bounded adapter timeout; stopping does not cancel the incident. Previous
signal handlers are restored on success and failure. Expected configuration
errors return exit 2 with a safe code. Unexpected DB/worker errors return exit 1
with WORKER_FAILED, never raw exception text, and are not automatically retried.
Normal completion returns exit 0 and safe polling counters. Leases/checkpoints
retain existing fenced crash recovery behavior.

Snapshot retention is disabled in this CLI: no encryption key is auto-loaded.
The separate explicit SnapshotKeyring API remains available to trusted callers;
key provisioning/recovery needs operator configuration before enabling retention.
No gh CLI token lookup, dotenv loading or ambient Nebius credential use occurs.

Acceptance uses a disposable real PostgreSQL runtime role and synthetic source
loader. It checks intake-to-TRIAGING, pre-dispatch signal stop, sanitized dispatch
failure, handler restoration and migration-role rejection. This does not prove
live full-worker GitHub access or a deployed background service. Supervisor,
restart/backoff, health monitoring, production auth and later-stage dispatch remain
pending. M1 is partial; TRIAGING remains a handoff, not a verified repair.
