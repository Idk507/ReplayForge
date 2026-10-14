"""Immutable redacted incident input bound to one durable run."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE incident_reports (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload)<=4096),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT
            (clock_timestamp()+interval '7 days')
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE incident_reports")
