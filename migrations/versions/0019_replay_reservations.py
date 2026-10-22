"""Immutable candidate/clean-replay intent and requested budget reservation."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE replay_reservations (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 32768),
        from_version bigint NOT NULL CHECK (from_version>=0),
        to_version bigint NOT NULL CHECK (to_version=from_version+1),
        expires_at timestamptz NOT NULL DEFAULT
            (clock_timestamp()+interval '24 hours'))""")


def downgrade() -> None:
    op.execute("DROP TABLE replay_reservations")
