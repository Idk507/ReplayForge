"""Append-only, bounded, unverified provider-operation lifecycle assertions."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE baseline_operation_records (
        execution_id uuid NOT NULL REFERENCES baseline_dispatch_intents(execution_id),
        sequence smallint NOT NULL CHECK (sequence BETWEEN 1 AND 3),
        phase text NOT NULL CHECK (
            (sequence=1 AND phase='SUBMISSION_UNKNOWN') OR
            (sequence=2 AND phase='OPERATION_KNOWN') OR
            (sequence=3 AND phase='COMPUTE_TERMINAL')),
        provider text NOT NULL CHECK (provider IN ('NEBIUS_CONTREE','TEST_TRANSPORT')),
        operation_id uuid,
        payload bytea NOT NULL CHECK (octet_length(payload)<=16384),
        digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        CHECK ((sequence=1 AND operation_id IS NULL) OR
               (sequence>1 AND operation_id IS NOT NULL)),
        PRIMARY KEY(execution_id,sequence)
    );
    CREATE UNIQUE INDEX baseline_operation_identity
    ON baseline_operation_records(provider,operation_id) WHERE sequence=2;""")


def downgrade() -> None:
    op.execute("DROP TABLE baseline_operation_records")
