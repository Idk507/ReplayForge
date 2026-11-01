"""Durable collection dispatch intents and append-only reconciliation audit."""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One immutable intent per fenced claim, committed before any broker request.
    op.execute("""CREATE TABLE collection_dispatches (
        operation_id uuid PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES runs(id),
        attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 3),
        worker_id uuid NOT NULL,
        lease_sha256 text NOT NULL CHECK (lease_sha256 ~ '^[a-f0-9]{64}$'),
        run_version integer NOT NULL CHECK (run_version >= 0),
        side_effect text NOT NULL CHECK (side_effect IN ('READ_ONLY_PINNED_COMMIT')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (run_id, lease_sha256))""")
    # SENT is committed before the request is initiated; its absence proves no send.
    op.execute("""CREATE TABLE collection_dispatch_events (
        operation_id uuid NOT NULL REFERENCES collection_dispatches(operation_id),
        kind text NOT NULL CHECK (kind IN ('SENT','COMPLETED','REJECTED')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (operation_id, kind))""")
    op.execute("""CREATE TABLE collection_reconciliations (
        id uuid PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES runs(id),
        job_attempt integer NOT NULL CHECK (job_attempt BETWEEN 0 AND 3),
        lease_sha256 text CHECK (lease_sha256 ~ '^[a-f0-9]{64}$'),
        operation_id uuid REFERENCES collection_dispatches(operation_id),
        observed_state text NOT NULL CHECK (observed_state IN
            ('CLAIMED_NO_INTENT','INTENT_NOT_SENT','SENT_UNCERTAIN',
             'COMPLETED','REJECTED','NOT_RECONCILABLE')),
        action text NOT NULL CHECK (action IN ('REQUEUE','FAIL','BLOCKED')),
        operator text NOT NULL CHECK (operator ~ '^[A-Za-z0-9._@-]{3,64}$'),
        reason text NOT NULL CHECK (char_length(reason) BETWEEN 8 AND 500),
        result_job_status text NOT NULL,
        result_stage text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    op.execute(
        "CREATE INDEX collection_dispatches_run ON collection_dispatches(run_id)"
    )
    op.execute(
        "CREATE INDEX collection_reconciliations_run "
        "ON collection_reconciliations(run_id)"
    )


def downgrade() -> None:
    # Dispatch intents and operator audit rows are evidence; keep them unless empty.
    if (
        op.get_bind()
        .exec_driver_sql(
            "SELECT EXISTS(SELECT 1 FROM collection_dispatches) OR "
            "EXISTS(SELECT 1 FROM collection_reconciliations)"
        )
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_COLLECTION_JOURNAL_BEFORE_DOWNGRADE")
    op.execute("DROP TABLE collection_reconciliations")
    op.execute("DROP TABLE collection_dispatch_events")
    op.execute("DROP TABLE collection_dispatches")
