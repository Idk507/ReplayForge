"""Safe request identity and operator estimate on existing sticky intents."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("triage_intents", "patch_intents"):
        op.execute(f"""ALTER TABLE {table} ADD COLUMN dispatch_json jsonb
            CHECK (dispatch_json IS NULL OR
            (jsonb_typeof(dispatch_json)='object' AND
             octet_length(dispatch_json::text)<=4096))""")


def downgrade() -> None:
    for table in ("triage_intents", "patch_intents"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN dispatch_json")
