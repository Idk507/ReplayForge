"""Append-only bounded reproduction proposals; no executable database content."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE reproduction_artifacts (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload)<=131072),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT
            (clock_timestamp()+interval '7 days')
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE reproduction_artifacts")
