"""Append-only human decisions and one-use publication intents."""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE run_events DROP CONSTRAINT run_events_event_type_check;
        ALTER TABLE run_events ADD CONSTRAINT run_events_event_type_check CHECK
        (event_type IN ('CREATED','STAGE_CHANGED','CANCELLED','LEASE_CLAIMED',
        'LEASE_EXHAUSTED','BUDGET_RESERVED','EVIDENCE_RETAINED','RESEARCH_RETAINED',
        'REPLAY_SUBMISSION_CLAIMED','REPLAY_OPERATION_BOUND',
        'REPLAY_OBSERVATION_RETAINED','REPLAY_PREPARATION_RETAINED',
        'REPLAY_RECEIPT_RETAINED','APPROVAL_RECORDED','PUBLICATION_CLAIMED'))""")
    op.execute("""CREATE TABLE approval_decisions (
        run_id uuid PRIMARY KEY REFERENCES runs(id), nonce uuid NOT NULL UNIQUE,
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 16384),
        from_version integer NOT NULL CHECK (from_version>=0),
        expires_at timestamptz NOT NULL)""")
    op.execute("""CREATE TABLE publication_intents (
        run_id uuid PRIMARY KEY REFERENCES approval_decisions(run_id),
        approval_nonce uuid NOT NULL UNIQUE REFERENCES approval_decisions(nonce),
        external_id uuid NOT NULL UNIQUE,
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 16384),
        from_version integer NOT NULL CHECK (from_version>=0))""")


def downgrade() -> None:
    op.execute("DROP TABLE publication_intents")
    op.execute("DROP TABLE approval_decisions")
