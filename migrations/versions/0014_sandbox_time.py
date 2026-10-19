"""Record the requested timeout charged with a baseline dispatch intent."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE baseline_dispatch_intents
        ADD COLUMN reserved_seconds integer
        CHECK (reserved_seconds BETWEEN 1 AND 60)""")


def downgrade() -> None:
    op.execute("ALTER TABLE baseline_dispatch_intents DROP COLUMN reserved_seconds")
