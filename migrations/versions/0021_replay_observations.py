"""Private immutable terminal replay observations, not authenticated receipts."""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE run_events DROP CONSTRAINT run_events_event_type_check;
        ALTER TABLE run_events ADD CONSTRAINT run_events_event_type_check CHECK
        (event_type IN ('CREATED','STAGE_CHANGED','CANCELLED','LEASE_CLAIMED',
        'LEASE_EXHAUSTED','BUDGET_RESERVED','EVIDENCE_RETAINED','RESEARCH_RETAINED',
        'REPLAY_SUBMISSION_CLAIMED','REPLAY_OPERATION_BOUND',
        'REPLAY_OBSERVATION_RETAINED'))""")
    op.execute("""CREATE TABLE replay_observations (
        run_id uuid NOT NULL,
        phase text NOT NULL,
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 100000),
        expires_at timestamptz NOT NULL,
        PRIMARY KEY(run_id,phase),
        FOREIGN KEY(run_id,phase)
        REFERENCES replay_submission_results(run_id,phase))""")


def downgrade() -> None:
    # Keep additive audit vocabulary for immutable historical events.
    op.execute("DROP TABLE replay_observations")
