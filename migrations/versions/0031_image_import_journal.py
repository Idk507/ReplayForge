"""Durable operator-side OCI import intents and observations."""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE image_import_attempts (
        id uuid PRIMARY KEY,
        project_id text NOT NULL CHECK(project_id ~ '^[A-Za-z0-9_.-]{1,128}$'),
        source_reference text NOT NULL CHECK(
            length(source_reference) BETWEEN 32 AND 512
        ),
        source_digest text NOT NULL CHECK(source_digest ~ '^[a-f0-9]{64}$'),
        request_digest text NOT NULL CHECK(request_digest ~ '^[a-f0-9]{64}$'),
        state text NOT NULL CHECK(state IN
            ('INTENT','SUBMISSION_UNKNOWN','PROVIDER_REJECTED',
             'OPERATION_KNOWN','TERMINAL')),
        operation_id uuid UNIQUE,
        result_image_uuid uuid,
        result_status text CHECK(result_status IN ('SUCCESS','FAILED','CANCELLED')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(project_id,source_digest),
        CHECK((state IN ('OPERATION_KNOWN','TERMINAL')) = (operation_id IS NOT NULL)),
        CHECK(result_status IS NULL OR state='TERMINAL'),
        CHECK(result_image_uuid IS NULL OR result_status='SUCCESS'),
        CHECK(result_status <> 'SUCCESS' OR result_image_uuid IS NOT NULL))""")
    op.execute("""CREATE TABLE image_import_events (
        attempt_id uuid NOT NULL REFERENCES image_import_attempts(id),
        sequence integer NOT NULL CHECK(sequence>=1),
        kind text NOT NULL CHECK(kind IN
            ('INTENT','DIAGNOSTIC','OPERATION_KNOWN','TERMINAL')),
        payload bytea NOT NULL CHECK(octet_length(payload)<=4096),
        digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(attempt_id,sequence))""")


def downgrade() -> None:
    if (
        op.get_bind()
        .exec_driver_sql("SELECT EXISTS(SELECT 1 FROM image_import_attempts)")
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_IMAGE_IMPORT_JOURNAL_BEFORE_DOWNGRADE")
    op.execute("DROP TABLE image_import_events,image_import_attempts")
