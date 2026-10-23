"""Immutable observed publication results; not approval or reusable authority."""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE publication_results (
        run_id uuid PRIMARY KEY REFERENCES publication_intents(run_id),
        external_id uuid NOT NULL UNIQUE,
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 8192),
        expires_at timestamptz NOT NULL
        DEFAULT (clock_timestamp()+interval '24 hours'))""")
    op.execute("""ALTER TABLE run_events DROP CONSTRAINT run_events_event_type_check;
        ALTER TABLE run_events ADD CONSTRAINT run_events_event_type_check CHECK
        (event_type IN ('CREATED','STAGE_CHANGED','CANCELLED','LEASE_CLAIMED',
        'LEASE_EXHAUSTED','BUDGET_RESERVED','EVIDENCE_RETAINED','RESEARCH_RETAINED',
        'REPLAY_SUBMISSION_CLAIMED','REPLAY_OPERATION_BOUND',
        'REPLAY_OBSERVATION_RETAINED','REPLAY_PREPARATION_RETAINED',
        'REPLAY_RECEIPT_RETAINED','APPROVAL_RECORDED','PUBLICATION_CLAIMED',
        'PUBLICATION_OBSERVED'))""")


def downgrade() -> None:
    op.execute("DROP TABLE publication_results")
