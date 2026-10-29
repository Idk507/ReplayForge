"""Admit an independently journaled existing-suite replay phase."""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Expand only the phase checks that constrain durable replay intent."""
    op.execute(
        "ALTER TABLE replay_submissions DROP CONSTRAINT replay_submissions_phase_check"
    )
    op.execute(
        "ALTER TABLE replay_submissions ADD CONSTRAINT replay_submissions_phase_check "
        "CHECK (phase IN ('CANDIDATE','EXISTING_SUITE','CLEAN_REPLAY'))"
    )
    op.execute(
        "ALTER TABLE patched_materialization_intents "
        "DROP CONSTRAINT patched_materialization_intents_phase_check"
    )
    op.execute(
        "ALTER TABLE patched_materialization_intents "
        "ADD CONSTRAINT patched_materialization_intents_phase_check "
        "CHECK (phase IN ('CANDIDATE','EXISTING_SUITE','CLEAN_REPLAY'))"
    )


def downgrade() -> None:
    """Refuse rollback if any suite preparation or dispatch is retained."""
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM replay_submissions WHERE phase='EXISTING_SUITE') "
        "OR EXISTS (SELECT 1 FROM patched_materialization_intents "
        "WHERE phase='EXISTING_SUITE') THEN "
        "RAISE EXCEPTION 'ARCHIVE_EXISTING_SUITE_EVIDENCE_BEFORE_DOWNGRADE'; "
        "END IF; END $$"
    )
    op.execute(
        "ALTER TABLE patched_materialization_intents "
        "DROP CONSTRAINT patched_materialization_intents_phase_check"
    )
    op.execute(
        "ALTER TABLE patched_materialization_intents "
        "ADD CONSTRAINT patched_materialization_intents_phase_check "
        "CHECK (phase IN ('CANDIDATE','CLEAN_REPLAY'))"
    )
    op.execute(
        "ALTER TABLE replay_submissions DROP CONSTRAINT replay_submissions_phase_check"
    )
    op.execute(
        "ALTER TABLE replay_submissions ADD CONSTRAINT replay_submissions_phase_check "
        "CHECK (phase IN ('CANDIDATE','CLEAN_REPLAY'))"
    )
