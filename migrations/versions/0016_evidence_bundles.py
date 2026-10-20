"""Private content-addressed evidence bundles with read expiry."""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE evidence_bundles (
        run_id uuid NOT NULL REFERENCES runs(id),
        manifest_digest text NOT NULL CHECK (manifest_digest ~ '^[a-f0-9]{64}$'),
        payload_digest text NOT NULL CHECK (payload_digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 3000000),
        expires_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '24 hours'),
        PRIMARY KEY (run_id,manifest_digest))""")
    op.execute("""ALTER TABLE run_events DROP CONSTRAINT run_events_event_type_check;
        ALTER TABLE run_events ADD CONSTRAINT run_events_event_type_check CHECK
        (event_type IN ('CREATED','STAGE_CHANGED','CANCELLED','LEASE_CLAIMED',
        'LEASE_EXHAUSTED','BUDGET_RESERVED','EVIDENCE_RETAINED'))""")


def downgrade() -> None:
    # Archive/review retained evidence before a production rollback.
    # Keep the additive audit vocabulary so existing immutable events stay valid.
    op.execute("DROP TABLE evidence_bundles")
