"""Append-only baseline premeasurements and produced receipt carriers."""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE baseline_action_events
        DROP CONSTRAINT baseline_action_events_kind_check;
        ALTER TABLE baseline_action_events ADD CONSTRAINT
        baseline_action_events_kind_check CHECK
        (kind IN ('PLANNED','RECEIPT_RETAINED','SUBMISSION_CLAIMED',
                  'PREPARATION_RETAINED','PRODUCED_RECEIPT_RETAINED'))""")
    op.execute("""CREATE TABLE baseline_preparations (
        execution_id uuid PRIMARY KEY REFERENCES baseline_submissions(execution_id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 32768),
        expires_at timestamptz NOT NULL)""")
    op.execute("""CREATE TABLE baseline_produced_receipts (
        execution_id uuid PRIMARY KEY REFERENCES baseline_preparations(execution_id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 100000),
        expires_at timestamptz NOT NULL)""")


def downgrade() -> None:
    if (
        op.get_bind()
        .exec_driver_sql(
            "SELECT EXISTS(SELECT 1 FROM baseline_preparations) "
            "OR EXISTS(SELECT 1 FROM baseline_produced_receipts) "
            "OR EXISTS(SELECT 1 FROM baseline_action_events WHERE kind IN "
            "('PREPARATION_RETAINED','PRODUCED_RECEIPT_RETAINED'))"
        )
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_BASELINE_PRODUCER_BEFORE_DOWNGRADE")
    op.execute("DROP TABLE baseline_produced_receipts, baseline_preparations")
    op.execute("""ALTER TABLE baseline_action_events
        DROP CONSTRAINT baseline_action_events_kind_check;
        ALTER TABLE baseline_action_events ADD CONSTRAINT
        baseline_action_events_kind_check CHECK
        (kind IN ('PLANNED','RECEIPT_RETAINED','SUBMISSION_CLAIMED'))""")
