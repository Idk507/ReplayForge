"""Retain individual wheel upload and spawn submission intents/results."""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_PREPARATION_KINDS = (
    "'INTENT','DIAGNOSTIC','UPLOAD_INTENT','UPLOAD_KNOWN','SPAWN_INTENT',"
    "'OPERATION_KNOWN','TERMINAL'"
)
_LEGACY_KINDS = "'INTENT','DIAGNOSTIC','OPERATION_KNOWN','TERMINAL'"


def upgrade() -> None:
    """Allow only the additional bounded lifecycle event types."""
    op.execute(
        "ALTER TABLE image_import_events DROP CONSTRAINT image_import_events_kind_check"
    )
    op.execute(
        "ALTER TABLE image_import_events ADD CONSTRAINT "
        "image_import_events_kind_check CHECK "
        f"(kind IN ({_PREPARATION_KINDS}))"
    )


def downgrade() -> None:
    """Refuse rollback while preparation audit events need the expanded check."""
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM image_import_events WHERE kind IN "
        "('UPLOAD_INTENT','UPLOAD_KNOWN','SPAWN_INTENT')) THEN "
        "RAISE EXCEPTION 'ARCHIVE_IMAGE_PREPARATION_EVENTS_BEFORE_DOWNGRADE'; "
        "END IF; END $$"
    )
    op.execute(
        "ALTER TABLE image_import_events DROP CONSTRAINT image_import_events_kind_check"
    )
    op.execute(
        "ALTER TABLE image_import_events ADD CONSTRAINT "
        "image_import_events_kind_check CHECK "
        f"(kind IN ({_LEGACY_KINDS}))"
    )
