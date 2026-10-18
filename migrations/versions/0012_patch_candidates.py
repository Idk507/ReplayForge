"""Append-only one-shot patch inference intent and admitted candidate bytes."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE patch_intents (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        attempt_id uuid NOT NULL UNIQUE,
        reserved_version bigint NOT NULL CHECK (reserved_version>0)
    );
    CREATE TABLE patch_candidates (
        run_id uuid PRIMARY KEY REFERENCES patch_intents(run_id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload)<=3100000),
        expires_at timestamptz NOT NULL DEFAULT
            (clock_timestamp()+interval '7 days')
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE patch_candidates; DROP TABLE patch_intents")
