"""Trusted repository grants; runtime receives SELECT only."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE repository_grants (
        id uuid PRIMARY KEY,
        owner_id uuid NOT NULL,
        grant_json jsonb NOT NULL CHECK (jsonb_typeof(grant_json)='object'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE repository_grants")
