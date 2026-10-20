"""Sticky documentation search intents and private reports; no provider dispatch."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE research_intents (
        run_id uuid NOT NULL REFERENCES runs(id),
        query_digest text NOT NULL CHECK (query_digest ~ '^[a-f0-9]{64}$'),
        reserved_version bigint NOT NULL CHECK (reserved_version>=0),
        PRIMARY KEY(run_id,query_digest));
        CREATE TABLE research_reports (
        run_id uuid NOT NULL,
        query_digest text NOT NULL,
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 65536),
        expires_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '24 hours'),
        PRIMARY KEY(run_id,query_digest),
        FOREIGN KEY(run_id,query_digest)
        REFERENCES research_intents(run_id,query_digest))""")
    op.execute("""ALTER TABLE run_events DROP CONSTRAINT run_events_event_type_check;
        ALTER TABLE run_events ADD CONSTRAINT run_events_event_type_check CHECK
        (event_type IN ('CREATED','STAGE_CHANGED','CANCELLED','LEASE_CLAIMED',
        'LEASE_EXHAUSTED','BUDGET_RESERVED','EVIDENCE_RETAINED','RESEARCH_RETAINED'))""")


def downgrade() -> None:
    # Preserve additive vocabulary for historical immutable audit events.
    op.execute("DROP TABLE research_reports; DROP TABLE research_intents")
