"""Bounded authenticated ciphertext only; runtime cannot rewrite/delete."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE sealed_snapshots (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload)<=4100000),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT
            (clock_timestamp()+interval '7 days')
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE sealed_snapshots")
