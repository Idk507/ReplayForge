"""Immutable checksummed repository index artifacts; runtime gets no UPDATE/DELETE."""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE repository_indexes (
        run_id uuid NOT NULL REFERENCES runs(id),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        payload bytea NOT NULL CHECK (octet_length(payload) <= 262144),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT
            (clock_timestamp()+interval '7 days'),
        PRIMARY KEY (run_id, digest))""")


def downgrade() -> None:
    # Retained analysis artifacts are evidence; keep them unless the table is empty.
    if (
        op.get_bind()
        .exec_driver_sql("SELECT EXISTS(SELECT 1 FROM repository_indexes)")
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_REPOSITORY_INDEXES_BEFORE_DOWNGRADE")
    op.execute("DROP TABLE repository_indexes")
