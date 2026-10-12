# ADR 012: PostgreSQL transactions and a local-only API

Status: implemented locally, production acceptance pending.

Use pinned Psycopg 3 for explicit SQL transactions and Alembic/SQLAlchemy for
versioned schema changes. JSONB stores the validated versioned RunState; runs,
events and job leases are the first migration. Full repository/artifact/approval
tables are deferred rather than represented by fake operational data.

Serialize idempotent creation using an owner/key advisory transaction lock plus
a unique constraint. Lock run and job rows together for SKIP LOCKED claims.
Use fresh lease tokens, server-clock expiry and version checks for stale-worker
fencing. Append allowlisted events in the same transaction as state changes.
Real concurrent tests found duplicate claims with run-only locking, and duplicate
creation contention required pre-insert serialization; both were corrected.

The user chose local development authentication. A configured bearer token maps
to a server-controlled owner/repository scope; remote clients are rejected.
This is not OIDC or public-deployment authentication. Intake discards raw incident
text after hashing until redacted artifact storage exists. A finite SSE page
supports cursor replay without retaining a database transaction during streaming.

Exactly-once cloud/GitHub effects cannot be guaranteed by database lease fencing
alone. Such dispatch remains disabled pending durable external-action IDs and
provider reconciliation. Exhausted jobs remain explicitly marked and audited;
automatic terminal failure routing and LangGraph integration are still pending.
