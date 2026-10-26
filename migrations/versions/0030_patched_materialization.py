"""Durable candidate and clean workspace materialization custody."""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE run_events DROP CONSTRAINT run_events_event_type_check;
        ALTER TABLE run_events ADD CONSTRAINT run_events_event_type_check CHECK
        (event_type IN ('CREATED','STAGE_CHANGED','CANCELLED','LEASE_CLAIMED',
        'LEASE_EXHAUSTED','BUDGET_RESERVED','EVIDENCE_RETAINED','RESEARCH_RETAINED',
        'REPLAY_SUBMISSION_CLAIMED','REPLAY_OPERATION_BOUND',
        'REPLAY_OBSERVATION_RETAINED','REPLAY_PREPARATION_RETAINED',
        'REPLAY_RECEIPT_RETAINED','APPROVAL_RECORDED','PUBLICATION_CLAIMED',
        'PUBLICATION_OBSERVED','MATERIALIZATION_INTENT_RECORDED',
        'MATERIALIZATION_OPERATION_KNOWN','MATERIALIZATION_MEASURED',
        'PATCH_WORKSPACE_INTENT_RECORDED','PATCH_WORKSPACE_OPERATION_KNOWN',
        'PATCH_WORKSPACE_MEASURED'))""")
    op.execute("""CREATE TABLE patched_materialization_intents (
        run_id uuid NOT NULL REFERENCES runs(id),
        phase text NOT NULL CHECK(phase IN ('CANDIDATE','CLEAN_REPLAY')),
        payload bytea NOT NULL, digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
        from_version integer NOT NULL CHECK(from_version>=0),
        origin text NOT NULL CHECK(origin IN ('TEST_TRANSPORT','REMOTE_SERVICE')),
        expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '1 hour',
        PRIMARY KEY(run_id,phase))""")
    op.execute("""CREATE TABLE patched_materialization_operations (
        run_id uuid NOT NULL, phase text NOT NULL,
        operation_id uuid NOT NULL UNIQUE,
        observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(run_id,phase),
        FOREIGN KEY(run_id,phase) REFERENCES
        patched_materialization_intents(run_id,phase))""")
    op.execute("""CREATE TABLE patched_materialization_results (
        run_id uuid NOT NULL, phase text NOT NULL,
        payload bytea NOT NULL,
        digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
        PRIMARY KEY(run_id,phase),
        FOREIGN KEY(run_id,phase) REFERENCES
        patched_materialization_operations(run_id,phase))""")


def downgrade() -> None:
    if (
        op.get_bind()
        .exec_driver_sql("SELECT EXISTS(SELECT 1 FROM patched_materialization_intents)")
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_PATCHED_MATERIALIZATION_INTENTS_BEFORE_DOWNGRADE")
    op.execute(
        "DROP TABLE patched_materialization_results,patched_materialization_operations,"
        "patched_materialization_intents"
    )
