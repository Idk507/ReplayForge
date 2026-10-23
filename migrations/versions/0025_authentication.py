"""Browser login custody; no execution grants or privileged repository authority."""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth_users (
        id uuid PRIMARY KEY, active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE TABLE auth_identities (
        github_id bigint PRIMARY KEY CHECK(github_id>0),
        user_id uuid NOT NULL UNIQUE REFERENCES auth_users(id),
        login text NOT NULL CHECK(length(login) BETWEEN 1 AND 39),
        avatar_url text NOT NULL CHECK(length(avatar_url)<=250));
    CREATE TABLE auth_oauth_attempts (
        state_hash text PRIMARY KEY CHECK(state_hash ~ '^[a-f0-9]{64}$'),
        browser_hash text NOT NULL CHECK(browser_hash ~ '^[a-f0-9]{64}$'),
        verifier_ciphertext bytea NOT NULL
        CHECK(octet_length(verifier_ciphertext)<=512),
        expires_at timestamptz NOT NULL
        DEFAULT(clock_timestamp()+interval '10 minutes'),
        claim uuid UNIQUE, consumed_at timestamptz, completed_at timestamptz);
    CREATE TABLE auth_credentials (
        user_id uuid PRIMARY KEY REFERENCES auth_users(id),
        access_ciphertext bytea NOT NULL CHECK(octet_length(access_ciphertext)<=9000),
        refresh_ciphertext bytea NOT NULL CHECK(octet_length(refresh_ciphertext)<=9000),
        expires_at timestamptz NOT NULL, refresh_expires_at timestamptz NOT NULL,
        rotation_version bigint NOT NULL DEFAULT 0 CHECK(rotation_version>=0));
    CREATE TABLE auth_sessions (
        secret_hash text PRIMARY KEY CHECK(secret_hash ~ '^[a-f0-9]{64}$'),
        user_id uuid NOT NULL REFERENCES auth_users(id),
        origin text NOT NULL CHECK(origin IN ('TEST_TRANSPORT','REMOTE_SERVICE')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL, revoked_at timestamptz);
    CREATE INDEX auth_session_user ON auth_sessions(user_id);
    CREATE TABLE auth_security_events (
        id uuid PRIMARY KEY, actor_id uuid REFERENCES auth_users(id),
        action text NOT NULL
        CHECK(action IN ('LOGIN_STARTED','LOGIN_COMPLETED','LOGOUT','AUTH_DENIED')),
        decision text NOT NULL DEFAULT 'ALLOW' CHECK(decision IN ('ALLOW','DENY')),
        reason_code text NOT NULL DEFAULT 'OK' CHECK(reason_code ~ '^[A-Z_]{1,64}$'),
        correlation_id uuid NOT NULL DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE TABLE auth_rate_limits (
        bucket text PRIMARY KEY CHECK(bucket ~ '^[a-f0-9]{64}$'),
        started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        hits integer NOT NULL CHECK(hits>0));
    """)


def downgrade() -> None:
    op.execute("""DROP TABLE auth_rate_limits; DROP TABLE auth_security_events;
        DROP TABLE auth_sessions; DROP TABLE auth_credentials;
        DROP TABLE auth_oauth_attempts; DROP TABLE auth_identities;
        DROP TABLE auth_users;""")
