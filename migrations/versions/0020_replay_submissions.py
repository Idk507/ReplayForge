"""Append-only replay submission claims and correlated provider identities."""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE run_events DROP CONSTRAINT run_events_event_type_check;
        ALTER TABLE run_events ADD CONSTRAINT run_events_event_type_check CHECK
        (event_type IN ('CREATED','STAGE_CHANGED','CANCELLED','LEASE_CLAIMED',
        'LEASE_EXHAUSTED','BUDGET_RESERVED','EVIDENCE_RETAINED','RESEARCH_RETAINED',
        'REPLAY_SUBMISSION_CLAIMED','REPLAY_OPERATION_BOUND'))""")
    op.execute("""CREATE TABLE replay_submissions (
        run_id uuid NOT NULL REFERENCES replay_reservations(run_id),
        phase text NOT NULL CHECK (phase IN ('CANDIDATE','CLEAN_REPLAY')),
        plan_ref text NOT NULL CHECK (plan_ref ~ '^sha256:[a-f0-9]{64}$'),
        capability_ref text NOT NULL CHECK (capability_ref ~ '^sha256:[a-f0-9]{64}$'),
        origin text NOT NULL CHECK (origin IN ('TEST_TRANSPORT','REMOTE_SERVICE')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(run_id,phase))""")
    op.execute("""CREATE TABLE replay_submission_results (
        run_id uuid NOT NULL,
        phase text NOT NULL,
        operation_id uuid NOT NULL UNIQUE,
        PRIMARY KEY(run_id,phase),
        FOREIGN KEY(run_id,phase) REFERENCES replay_submissions(run_id,phase))""")


def downgrade() -> None:
    # Preserve additive vocabulary for historical immutable audit events.
    op.execute("DROP TABLE replay_submission_results")
    op.execute("DROP TABLE replay_submissions")
