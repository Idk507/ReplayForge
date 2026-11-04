"""Production cookie and OAuth boundaries with a synthetic provider and test DB."""

from urllib.parse import parse_qs, urlsplit

from pydantic import SecretStr

from services.auth.settings import GitHubAuthSettings
from tests.integration.test_auth_app import client, isolated_browser_rate_bucket, store

__all__ = ["isolated_browser_rate_bucket", "store"]


def test_https_oauth_session_csrf_and_replay(store):
    config = GitHubAuthSettings(
        client_id="Iv1.synthetic",
        client_secret=SecretStr("synthetic-client-secret"),
        public_origin="https://demo.example.com",
        web_origin="https://demo.example.com",
        development=False,
    )
    browser, calls = client(store, config)
    assert browser.get("/api/v1/auth/me").status_code == 401
    start = browser.get("/api/v1/auth/github/start", follow_redirects=False)
    assert start.status_code == 302
    cookie = start.headers["set-cookie"]
    assert "__Host-rf_oauth=" in cookie and "HttpOnly" in cookie
    assert "Secure" in cookie and "SameSite=lax" in cookie
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = browser.get(
        "/api/v1/auth/github/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    session_cookie = callback.headers.get_list("set-cookie")[0]
    assert "__Host-rf_session=" in session_cookie and "HttpOnly" in session_cookie
    assert "Secure" in session_cookie and "SameSite=lax" in session_cookie
    me = browser.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["github_id"] == 123
    token = me.json()["csrf_token"]
    assert browser.post("/api/v1/auth/logout").status_code == 403
    assert (
        browser.post(
            "/api/v1/auth/logout",
            headers={"origin": "https://evil.example", "x-csrf-token": token},
        ).status_code
        == 403
    )
    assert (
        browser.post(
            "/api/v1/auth/logout",
            headers={"origin": "https://demo.example.com", "x-csrf-token": token},
        ).status_code
        == 204
    )
    assert browser.get("/api/v1/auth/me").status_code == 401
    assert (
        browser.get(
            "/api/v1/auth/github/callback",
            params={"state": state, "code": "replayed-code"},
            follow_redirects=False,
        ).status_code
        == 401
    )
    assert len(calls) == 2
