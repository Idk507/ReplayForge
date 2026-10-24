"""Retain the provider membership page used for explicit connection admission."""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE auth_repository_connections ADD COLUMN repository_page integer "
        "CHECK(repository_page BETWEEN 1 AND 20000)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE auth_repository_connections DROP COLUMN repository_page")
