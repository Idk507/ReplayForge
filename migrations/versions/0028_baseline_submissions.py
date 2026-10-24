"""Immutable baseline submission claims; operation bindings remain append-only."""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE baseline_submissions (
        execution_id uuid PRIMARY KEY
        REFERENCES baseline_dispatch_intents(execution_id),
        plan_ref text NOT NULL CHECK (plan_ref ~ '^sha256:[a-f0-9]{64}$'),
        capability_ref text NOT NULL CHECK (capability_ref ~ '^sha256:[a-f0-9]{64}$'),
        runner_id uuid NOT NULL,
        configuration_ref text NOT NULL
        CHECK (configuration_ref ~ '^sha256:[a-f0-9]{64}$'),
        origin text NOT NULL CHECK (origin IN ('TEST_TRANSPORT','REMOTE_SERVICE')),
        expires_at timestamptz NOT NULL)""")
    op.execute("""ALTER TABLE baseline_action_events
        DROP CONSTRAINT baseline_action_events_kind_check;
        ALTER TABLE baseline_action_events ADD CONSTRAINT
        baseline_action_events_kind_check CHECK
        (kind IN ('PLANNED','RECEIPT_RETAINED','SUBMISSION_CLAIMED'))""")


def downgrade() -> None:
    # Historical audit rows must not be silently removed during rollback.
    if (
        op.get_bind()
        .exec_driver_sql("SELECT EXISTS(SELECT 1 FROM baseline_submissions)")
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_BASELINE_SUBMISSIONS_BEFORE_DOWNGRADE")
    op.execute("DROP TABLE baseline_submissions")
