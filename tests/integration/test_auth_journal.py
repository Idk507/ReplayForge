"""OAuth custody with synthetic GitHub identities in disposable PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from pydantic import SecretBytes, SecretStr

from services.auth.github import AuthDenied, GitHubUser, UserToken
from services.auth.journal import AuthJournal, AuthVault
from tests.integration import test_postgres

store = test_postgres.store


def journal(store):
    return AuthJournal(store, AuthVault(SecretBytes(b"e" * 32), SecretBytes(b"h" * 32)))


def identity(login="person"):
    return GitHubUser(
        id=123, login=login, avatar_url="https://avatars.githubusercontent.com/u/123"
    )


def tokens():
    return UserToken(
        access_token=SecretStr("synthetic-access"),
        refresh_token=SecretStr("synthetic-refresh"),
        token_type="bearer",
        expires_in=28800,
        refresh_token_expires_in=15897600,
    )


def login(j, who=None):
    attempt = j.start(peer=str(uuid4()))
    claim = j.consume(attempt.state, attempt.browser)
    return j.finish(claim, who or identity(), tokens(), origin="TEST_TRANSPORT")


def test_auth_session_restart_numeric_identity_and_logout(store):
    j = journal(store)
    first = login(j)
    second = login(j, identity("renamed"))
    assert first.user_id == second.user_id
    assert (
        journal(store).session(second.secret, allow_test=True).github_login == "renamed"
    )
    with pytest.raises(AuthDenied):
        j.session(first.secret)
    j.logout(first.secret)
    j.logout(first.secret)
    with pytest.raises(AuthDenied):
        j.session(first.secret, allow_test=True)
    assert j.session(second.secret, allow_test=True).id == first.user_id
    with store._connection() as c:
        row = c.execute(
            "SELECT access_ciphertext,refresh_ciphertext FROM "
            "auth_credentials WHERE user_id=%s",
            (first.user_id,),
        ).fetchone()
        assert b"synthetic" not in bytes(row["access_ciphertext"])
        assert b"synthetic" not in bytes(row["refresh_ciphertext"])


@pytest.mark.parametrize("fault", ["browser", "replay", "expiry", "unknown"])
def test_oauth_attempt_invalidations(store, fault):
    j = journal(store)
    a = j.start(peer=str(uuid4()))
    if fault == "expiry":
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as c:
            c.execute(
                "UPDATE auth_oauth_attempts SET expires_at=clock_timestamp() "
                "WHERE state_hash=%s",
                (j.vault.digest("state", a.state),),
            )
    if fault == "replay":
        j.consume(a.state, a.browser)
    with pytest.raises(AuthDenied):
        j.consume(
            SecretStr("x" * 43) if fault == "unknown" else a.state,
            SecretStr("x" * 43) if fault == "browser" else a.browser,
        )


def test_oauth_claim_cannot_create_two_sessions_even_concurrently(store):
    j = journal(store)
    a = j.start(peer=str(uuid4()))
    claim = j.consume(a.state, a.browser)

    def finish(_):
        try:
            return journal(store).finish(
                claim, identity(), tokens(), origin="TEST_TRANSPORT"
            )
        except AuthDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as p:
        results = list(p.map(finish, range(2)))
    assert sum(r is not None for r in results) == 1


def test_session_expiry_and_rotation(store):
    j = journal(store)
    first = login(j)
    a = j.start(peer=str(uuid4()))
    claim = j.consume(a.state, a.browser)
    second = j.finish(
        claim, identity(), tokens(), origin="TEST_TRANSPORT", old_session=first.secret
    )
    with pytest.raises(AuthDenied):
        j.session(first.secret, allow_test=True)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as c:
        c.execute(
            "UPDATE auth_sessions SET expires_at=clock_timestamp() WHERE "
            "secret_hash=%s",
            (j.vault.digest("session", second.secret),),
        )
    with pytest.raises(AuthDenied):
        j.session(second.secret, allow_test=True)


def test_auth_rate_limit_is_durable_and_one_time_claim_is_sticky(store):
    j = journal(store)
    for _ in range(10):
        j.rate("bounded-test-peer", "login-start")
    with pytest.raises(AuthDenied, match="AUTH_RATE_LIMITED"):
        journal(store).rate("bounded-test-peer", "login-start")
    a = j.start(peer=str(uuid4()))
    j.consume(a.state, a.browser)
    with pytest.raises(AuthDenied):
        journal(store).consume(a.state, a.browser)


def test_auth_accounts_never_inherit_legacy_owner_identity(store):
    j = journal(store)
    first = login(j)
    second = login(j, identity().model_copy(update={"id": 456}))
    assert first.user_id != second.user_id
    assert first.secret != second.secret
    assert j.session(second.secret, allow_test=True).github_id == 456


def test_auth_encryption_binds_purpose_and_rejects_wrong_keys(store):
    j = journal(store)
    sealed = j.vault.seal(SecretStr("private"), "one-record")
    with pytest.raises(AuthDenied):
        j.vault.open(sealed, "another-record")
    other = AuthVault(SecretBytes(b"x" * 32), SecretBytes(b"y" * 32))
    with pytest.raises(AuthDenied):
        other.open(sealed, "one-record")
    with pytest.raises(AuthDenied):
        j.vault.open(sealed[:-1] + b"0", "one-record")
