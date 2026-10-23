"""Loopback OAuth/session HTTP tests with a synthetic provider only."""

import os
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from services.auth.app import create_auth_app
from services.auth.github import GitHubAuthClient
from tests.contract.test_github_auth import settings
from tests.integration import test_postgres
from tests.integration.test_auth_journal import journal

store = test_postgres.store


@pytest.fixture(autouse=True)
def isolated_browser_rate_bucket(store):
    # Each case simulates a fresh test deployment. Preserve real durable rate rules.
    vault = journal(store).vault
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        for action in ("login-start", "login-callback"):
            conn.execute(
                "DELETE FROM auth_rate_limits WHERE bucket=%s",
                (vault.digest(action, SecretStr("127.0.0.1")),),
            )


def client(store, config=None, fault=None):
    config = config or settings()
    calls = []

    def handle(r):
        calls.append(r)
        if fault == "timeout":
            raise httpx.ReadTimeout("private", request=r)
        if r.url.host == "github.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "expires_in": 28800,
                    "refresh_token_expires_in": 15897600,
                    "token_type": "bearer",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 123,
                "login": "person",
                "avatar_url": "https://avatars.githubusercontent.com/u/123",
            },
        )

    app = create_auth_app(
        journal(store),
        config,
        lambda: GitHubAuthClient(config, transport=httpx.MockTransport(handle)),
        allow_test=True,
    )
    return TestClient(
        app, base_url=config.public_origin, client=("127.0.0.1", 50000)
    ), calls


def start(c):
    r = c.get("/api/v1/auth/github/start", follow_redirects=False)
    assert r.status_code == 302
    return parse_qs(urlsplit(r.headers["location"]).query)["state"][0]


def test_auth_browser_login_me_csrf_logout_and_replay(store):
    c, calls = client(store)
    assert c.get("/api/v1/auth/me").status_code == 401
    state = start(c)
    assert (
        c.get(
            "/api/v1/auth/github/callback",
            params={"state": state, "code": "synthetic-code"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    me = c.get("/api/v1/auth/me")
    assert (
        me.json()["github_login"] == "person"
        and me.headers["cache-control"] == "no-store"
    )
    assert len(calls) == 2
    assert "synthetic-access" not in me.text
    assert c.post("/api/v1/auth/logout").status_code == 403
    csrf = me.json()["csrf_token"]
    assert (
        c.post(
            "/api/v1/auth/logout",
            headers={"origin": "http://evil.example", "x-csrf-token": csrf},
        ).status_code
        == 403
    )
    response = c.post(
        "/api/v1/auth/logout",
        headers={"origin": "http://127.0.0.1:3000", "x-csrf-token": csrf},
    )
    assert response.status_code == 204
    assert c.get("/api/v1/auth/me").status_code == 401
    assert (
        c.get(
            "/api/v1/auth/github/callback",
            params={"state": state, "code": "same-code"},
            follow_redirects=False,
        ).status_code
        == 401
    )
    assert len(calls) == 2


@pytest.mark.parametrize("fault", ["browser", "state", "denied", "duplicate", "host"])
def test_auth_bad_callback_never_exchanges_code(store, fault):
    c, calls = client(store)
    state = start(c)
    params = [("state", state), ("code", "secret-code")]
    if fault == "browser":
        c.cookies.clear()
    if fault == "state":
        params[0] = ("state", "x" * 43)
    if fault == "denied":
        params.append(("error", "access_denied"))
    if fault == "duplicate":
        params.append(("state", state))
    headers = {"host": "evil.example"} if fault == "host" else {}
    r = c.get(
        "/api/v1/auth/github/callback",
        params=params,
        headers=headers,
        follow_redirects=False,
    )
    assert r.status_code in {400, 401, 403}
    assert not calls and "secret-code" not in r.text


def test_auth_session_boundary_has_no_legacy_bearer_or_unprotected_run_routes(store):
    c, _ = client(store)
    assert c.post("/api/v1/runs").status_code == 404
    assert (
        c.get(
            "/api/v1/auth/me", headers={"authorization": "Bearer arbitrary"}
        ).status_code
        == 401
    )


def test_auth_invalid_csrf_and_cookies_are_safe_and_audited(store):
    c, _ = client(store)
    state = start(c)
    c.get(
        "/api/v1/auth/github/callback",
        params={"state": state, "code": "synthetic"},
        follow_redirects=False,
    )
    response = c.post(
        "/api/v1/auth/logout",
        headers={"origin": "http://127.0.0.1:3000", "x-csrf-token": "invalid"},
    )
    assert response.status_code == 403
    with journal(store).store._connection() as conn:
        row = conn.execute(
            "SELECT reason_code,decision FROM auth_security_events "
            "WHERE action='AUTH_DENIED' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row == {"reason_code": "AUTH_CSRF_DENIED", "decision": "DENY"}
    c.cookies.set("rf_dev_session", "malformed")
    assert c.get("/api/v1/auth/me").status_code == 401


def test_auth_cookie_flags_and_independent_browsers(store):
    first, _ = client(store)
    second, _ = client(store)
    state = start(first)
    assert (
        "HttpOnly"
        in first.get("/api/v1/auth/github/start", follow_redirects=False).headers[
            "set-cookie"
        ]
    )
    assert (
        second.get(
            "/api/v1/auth/github/callback",
            params={"state": state, "code": "synthetic"},
            follow_redirects=False,
        ).status_code
        == 401
    )
    # Failed callback in another browser cannot consume the real browser's attempt.
    r = first.get(
        "/api/v1/auth/github/callback",
        params={"state": state, "code": "synthetic"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        "HttpOnly" in r.headers["set-cookie"]
        and "SameSite=lax" in r.headers["set-cookie"]
    )
    assert second.get("/api/v1/auth/me").status_code == 401


def test_auth_unknown_exchange_requires_fresh_login_not_retry(store):
    c, calls = client(store, fault="timeout")
    state = start(c)
    params = {"state": state, "code": "private-code"}
    assert (
        c.get(
            "/api/v1/auth/github/callback", params=params, follow_redirects=False
        ).status_code
        == 401
    )
    assert (
        c.get(
            "/api/v1/auth/github/callback", params=params, follow_redirects=False
        ).status_code
        == 401
    )
    assert len(calls) == 1
    assert "rf_dev_session" not in c.cookies


def test_auth_https_cookie_has_host_prefix_secure_and_no_domain(store):
    config = settings().model_copy(
        update={
            "development": False,
            "public_origin": "https://api.example.com",
            "web_origin": "https://app.example.com",
        }
    )
    c, _ = client(store, config=config)
    state = start(c)
    r = c.get(
        "/api/v1/auth/github/callback",
        params={"state": state, "code": "synthetic"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cookie = r.headers.get_list("set-cookie")[0]
    assert cookie.startswith("__Host-rf_session=")
    assert "Secure" in cookie and "HttpOnly" in cookie and "Domain=" not in cookie
    assert c.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize(
    "issuer",
    [
        "https://github.com/login/oauth",
        "https://evil.example",
        "https://github.com/login/oauth/",
    ],
)
def test_callback_validates_provider_issuer_before_exchange(store, issuer):
    c, calls = client(store)
    state = start(c)
    r = c.get(
        "/api/v1/auth/github/callback",
        params={"state": state, "code": "synthetic-code", "iss": issuer},
        follow_redirects=False,
    )
    assert r.status_code == (303 if issuer == "https://github.com/login/oauth" else 401)
    assert len(calls) == (2 if issuer == "https://github.com/login/oauth" else 0)
