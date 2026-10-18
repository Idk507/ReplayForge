"""Sticky inference intent prevents blind resubmission after worker loss."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE triage_intents (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        attempt_id uuid NOT NULL UNIQUE,
        context_digest text NOT NULL CHECK (context_digest ~ '^[a-f0-9]{64}$'),
        from_version bigint NOT NULL CHECK (from_version>=0),
        to_version bigint NOT NULL CHECK (to_version=from_version+1),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE triage_intents")
