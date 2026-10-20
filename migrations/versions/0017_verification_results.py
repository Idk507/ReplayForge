"""Append-only authenticated verdict records with private read expiry."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE verification_results (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 32768),
        expires_at timestamptz NOT NULL
        DEFAULT (clock_timestamp()+interval '24 hours'))""")


def downgrade() -> None:
    # Review/archive accepted evidence before a production rollback.
    op.execute("DROP TABLE verification_results")
