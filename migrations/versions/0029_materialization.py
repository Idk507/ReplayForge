"""Append-only source materialization custody; no rollback of live intents."""

from alembic import op

revision = "0029"
down_revision = "0028"
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
        'MATERIALIZATION_OPERATION_KNOWN','MATERIALIZATION_MEASURED'))""")
    op.execute("""CREATE TABLE materialization_intents (
        run_id uuid PRIMARY KEY REFERENCES runs(id),
        payload bytea NOT NULL, digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
        from_version integer NOT NULL CHECK(from_version>=0),
        origin text NOT NULL CHECK(origin IN ('TEST_TRANSPORT','REMOTE_SERVICE')),
        expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '1 hour')""")
    op.execute("""CREATE TABLE materialization_operations (
        run_id uuid PRIMARY KEY REFERENCES materialization_intents(run_id),
        operation_id uuid NOT NULL UNIQUE,
        observed_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    op.execute("""CREATE TABLE materialization_results (
        run_id uuid PRIMARY KEY REFERENCES materialization_operations(run_id),
        payload bytea NOT NULL,
        digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'))""")


def downgrade() -> None:
    if (
        op.get_bind()
        .exec_driver_sql("SELECT EXISTS(SELECT 1 FROM materialization_intents)")
        .scalar()
    ):
        raise RuntimeError("ARCHIVE_MATERIALIZATION_INTENTS_BEFORE_DOWNGRADE")
    op.execute(
        "DROP TABLE materialization_results,materialization_operations,"
        "materialization_intents"
    )
