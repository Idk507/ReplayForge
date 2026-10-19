"""Retain bounded signed baseline report bytes for restart-safe acceptance."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE baseline_receipts ADD COLUMN report_payload bytea
        CHECK (octet_length(report_payload) BETWEEN 1 AND 131072)""")


def downgrade() -> None:
    # Production rollback requires archival review: this discards retained reports.
    op.execute("ALTER TABLE baseline_receipts DROP COLUMN report_payload")
