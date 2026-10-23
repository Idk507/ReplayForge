"""Offline GitHub authentication boundary tests, never real credentials."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from services.auth.github import AuthDenied, GitHubAuthClient
from services.auth.settings import GitHubAuthSettings


def settings(**changes):
    return GitHubAuthSettings(
        client_id="Iv1.synthetic",
        client_secret=SecretStr("synthetic-secret"),
        public_origin="http://127.0.0.1:8000",
        web_origin="http://127.0.0.1:3000",
        development=True,
        **changes,
    )


def test_pkce_url_has_only_fixed_callback_and_no_credentials():
    with GitHubAuthClient(
        settings(), transport=httpx.MockTransport(lambda _: pytest.fail("I/O"))
    ) as c:
        url = c.authorize_url("a" * 43, "b" * 43)
    q = parse_qs(urlsplit(url).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/v1/auth/github/callback"]
    assert "synthetic-secret" not in url and "repo" not in q.get("scope", [])


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://example.com/path",
        "https://user:pass@example.com",
        "https://example.com?x=1",
        "https://example.com/#x",
        "https://example.com\\@evil.example",
        "http://localhost:8000",
    ],
)
def test_invalid_or_noncanonical_origins_fail(origin):
    with pytest.raises(ValidationError):
        GitHubAuthSettings(
            client_id="x",
            client_secret=SecretStr("secret"),
            public_origin=origin,
            web_origin="https://example.com",
        )


def test_exchange_and_numeric_identity_use_headers_and_body_only():
    calls = []

    def handle(r):
        calls.append(r)
        assert not r.url.query
        if r.url.host == "github.com":
            assert r.method == "POST"
            body = parse_qs(r.content.decode())
            assert body["code_verifier"] == ["v" * 43]
            assert body["client_secret"] == ["synthetic-secret"]
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-user-token",
                    "token_type": "bearer",
                    "expires_in": 28800,
                    "refresh_token": "synthetic-refresh",
                    "refresh_token_expires_in": 15897600,
                },
            )
        assert r.url == httpx.URL("https://api.github.com/user")
        assert r.headers["authorization"] == "Bearer synthetic-user-token"
        return httpx.Response(
            200,
            json={
                "id": 123,
                "login": "renamable",
                "avatar_url": "https://avatars.githubusercontent.com/u/123",
                "email": "ignored",
            },
        )

    with GitHubAuthClient(settings(), transport=httpx.MockTransport(handle)) as c:
        token = c.exchange(SecretStr("code"), SecretStr("v" * 43))
        user = c.user(token.access_token)
    assert user.id == 123 and len(calls) == 2
    assert "synthetic-user-token" not in repr(token)
    assert "synthetic-refresh" not in repr(token)
    assert "email" not in user.model_dump()


@pytest.mark.parametrize(
    "fault",
    [
        "timeout",
        "redirect",
        "auth",
        "rate",
        "server",
        "bad_json",
        "oversize",
        "compressed",
        "missing_id",
        "bool_id",
        "negative_id",
        "avatar",
    ],
)
def test_provider_failures_are_bounded_sanitized_and_never_retried(fault):
    calls = []

    def handle(r):
        calls.append(r)
        if fault == "timeout":
            raise httpx.ReadTimeout("secret", request=r)
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.example"})
        if fault in ["auth", "rate", "server"]:
            return httpx.Response(
                {"auth": 401, "rate": 429, "server": 500}[fault], text="secret"
            )
        if fault == "bad_json":
            return httpx.Response(200, text="secret")
        if fault == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        if fault == "compressed":
            return httpx.Response(200, headers={"content-encoding": "br"}, content=b"")
        return httpx.Response(
            200,
            json={
                "id": {"missing_id": None, "bool_id": True, "negative_id": -1}.get(
                    fault, 123
                ),
                "login": "person",
                "avatar_url": (
                    "https://evil.example/tracker"
                    if fault == "avatar"
                    else "https://avatars.githubusercontent.com/u/123"
                ),
            },
        )

    with GitHubAuthClient(settings(), transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(AuthDenied) as e:
            c.user(SecretStr("private-key"))
    assert "secret" not in str(e.value) and "private-key" not in str(e.value)
    assert len(calls) == 1


def test_auth_real_http_requires_explicit_runtime_opt_in():
    with GitHubAuthClient(settings()) as c:
        with pytest.raises(AuthDenied, match="AUTH_LIVE_NOT_AUTHORIZED"):
            c.exchange(SecretStr("code"), SecretStr("v" * 43))
