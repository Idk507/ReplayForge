"""Retain the exact canonical signed gate envelope and profile version."""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE verification_results "
        "ADD COLUMN envelope_version smallint, "
        "ADD COLUMN envelope_bytes bytea"
    )
    op.execute(
        "ALTER TABLE verification_results ADD CONSTRAINT "
        "verification_results_envelope_version_check CHECK "
        "(envelope_version IS NULL OR envelope_version IN (4,5))"
    )
    op.execute(
        "ALTER TABLE verification_results ADD CONSTRAINT "
        "verification_results_envelope_bytes_check CHECK "
        "(envelope_bytes IS NULL OR octet_length(envelope_bytes) BETWEEN 1 AND 16384)"
    )
    op.execute(
        "ALTER TABLE verification_results ADD CONSTRAINT "
        "verification_results_envelope_pair_check CHECK "
        "((envelope_version IS NULL) = (envelope_bytes IS NULL))"
    )


def downgrade() -> None:
    # Never discard retained signed envelope bytes during an automatic rollback.
    raise RuntimeError("0036 downgrade requires an evidence archival procedure")
