"""Bounded immutable sanitized context; application runtime gets no UPDATE/DELETE."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE context_artifacts (
        run_id uuid NOT NULL REFERENCES runs(id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload)<=4096),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT
            (clock_timestamp()+interval '7 days'),
        PRIMARY KEY(run_id,digest)
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE context_artifacts")
