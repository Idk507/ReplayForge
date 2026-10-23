"""Owner-scoped links to immutable execution grants; no legacy-owner adoption."""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE auth_repository_connections (
        repository_id uuid PRIMARY KEY REFERENCES repository_grants(id),
        user_id uuid NOT NULL REFERENCES auth_users(id),
        github_repository_id bigint NOT NULL CHECK(github_repository_id>0),
        installation_id bigint NOT NULL CHECK(installation_id>0),
        full_name text NOT NULL CHECK(length(full_name) BETWEEN 3 AND 140),
        source_commit text NOT NULL CHECK(source_commit ~ '^[a-f0-9]{40}$'),
        origin text NOT NULL CHECK(origin IN ('REMOTE_SERVICE','TEST_TRANSPORT')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        revoked_at timestamptz);
        CREATE UNIQUE INDEX auth_active_repo_connection ON
        auth_repository_connections(user_id,github_repository_id,source_commit)
        WHERE revoked_at IS NULL;""")


def downgrade() -> None:
    op.execute("DROP TABLE auth_repository_connections")
