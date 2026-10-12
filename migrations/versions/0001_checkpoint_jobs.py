"""Atomic checkpoint/event/job storage; no external execution records yet."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE runs (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL,
            idempotency_key uuid NOT NULL,
            request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
            checkpoint jsonb NOT NULL CHECK (jsonb_typeof(checkpoint) = 'object'),
            version bigint NOT NULL CHECK (version >= 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(owner_id, idempotency_key)
        );
        CREATE TABLE run_events (
            run_id uuid NOT NULL REFERENCES runs(id),
            sequence bigint NOT NULL CHECK (sequence >= 1),
            event_type text NOT NULL CHECK (event_type IN
                ('CREATED','STAGE_CHANGED','CANCELLED',
                 'LEASE_CLAIMED','LEASE_EXHAUSTED','BUDGET_RESERVED')),
            public_payload jsonb NOT NULL,
            trace_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(run_id, sequence)
        );
        CREATE TABLE job_leases (
            run_id uuid PRIMARY KEY REFERENCES runs(id),
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','leased','done','exhausted')),
            worker_id uuid,
            token uuid,
            leased_until timestamptz,
            attempt_count integer NOT NULL DEFAULT 0
                CHECK (attempt_count BETWEEN 0 AND 3),
            CHECK (status <> 'leased' OR (worker_id IS NOT NULL
                AND token IS NOT NULL AND leased_until IS NOT NULL))
        );
        CREATE INDEX job_claim_index ON job_leases(status, leased_until);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE job_leases; DROP TABLE run_events; DROP TABLE runs;")
