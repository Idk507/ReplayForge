"""Retain exact signed runner capability payloads for trusted gate rechecks."""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical rows intentionally remain NULL: a digest cannot reconstruct or
    # authenticate the assessor's original signed claims.
    op.execute("ALTER TABLE baseline_submissions ADD COLUMN capability_payload bytea")
    op.execute(
        "ALTER TABLE baseline_submissions ADD CONSTRAINT "
        "baseline_capability_payload_size CHECK (capability_payload IS NULL OR "
        "octet_length(capability_payload) BETWEEN 1 AND 16384)"
    )
    op.execute("ALTER TABLE replay_submissions ADD COLUMN capability_payload bytea")
    op.execute(
        "ALTER TABLE replay_submissions ADD CONSTRAINT "
        "replay_capability_payload_size CHECK (capability_payload IS NULL OR "
        "octet_length(capability_payload) BETWEEN 1 AND 16384)"
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .exec_driver_sql(
            "SELECT EXISTS(SELECT 1 FROM baseline_submissions "
            "WHERE capability_payload IS NOT NULL) OR "
            "EXISTS(SELECT 1 FROM replay_submissions "
            "WHERE capability_payload IS NOT NULL)"
        )
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_BASELINE_CAPABILITY_PAYLOADS_BEFORE_DOWNGRADE")
    op.execute(
        "ALTER TABLE baseline_submissions DROP CONSTRAINT "
        "baseline_capability_payload_size"
    )
    op.execute("ALTER TABLE baseline_submissions DROP COLUMN capability_payload")
    op.execute(
        "ALTER TABLE replay_submissions DROP CONSTRAINT replay_capability_payload_size"
    )
    op.execute("ALTER TABLE replay_submissions DROP COLUMN capability_payload")
