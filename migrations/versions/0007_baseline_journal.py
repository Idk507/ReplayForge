"""Append-only baseline plans, signed receipt metadata and correlated audit."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE baseline_actions (
        execution_id uuid PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES runs(id),
        plan_ref text NOT NULL,
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload)<=16384),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '7 days'),
        UNIQUE(run_id,plan_ref)
    );
    CREATE TABLE baseline_receipts (
        execution_id uuid PRIMARY KEY REFERENCES baseline_actions(execution_id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload)<=16384),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '7 days')
    );
    CREATE TABLE baseline_action_events (
        execution_id uuid NOT NULL REFERENCES baseline_actions(execution_id),
        kind text NOT NULL CHECK (kind IN ('PLANNED','RECEIPT_RETAINED')),
        run_version bigint NOT NULL CHECK (run_version>=0),
        trace_id uuid NOT NULL,
        worker_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(execution_id,kind)
    );""")


def downgrade() -> None:
    op.execute(
        "DROP TABLE baseline_action_events; DROP TABLE baseline_receipts; "
        "DROP TABLE baseline_actions"
    )
