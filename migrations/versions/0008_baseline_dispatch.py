"""Durable baseline reservation identity, without execution permission."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE baseline_dispatch_intents (
        execution_id uuid PRIMARY KEY REFERENCES baseline_actions(execution_id),
        action_digest text NOT NULL CHECK (action_digest ~ '^[a-f0-9]{64}$'),
        from_version bigint NOT NULL CHECK (from_version>=0),
        to_version bigint NOT NULL CHECK (to_version=from_version+1),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE baseline_dispatch_intents")
