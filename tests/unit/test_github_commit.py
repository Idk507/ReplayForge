"""Offline HTTP transports cannot prove live GitHub access."""

from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from packages.contracts.repository import RepositoryGrant
from services.repository.github import GitHubReader, RepositoryReadError


def grant() -> RepositoryGrant:
    return RepositoryGrant(
        repository_id=uuid4(),
        owner_id=uuid4(),
        remote_url="https://github.com/example/fixture",
        commits=("a" * 40,),
        approved_paths=("src/",),
        consent=True,
    )


def test_resolver_is_pinned_authenticated_and_labeled_test_transport() -> None:
    scope = grant()
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "sha": "a" * 40,
                "tree": {"sha": "b" * 40},
                "url": "http://127.0.0.1/ignore-me",
                "message": "untrusted secret body",
            },
        )

    with GitHubReader(
        SecretStr("synthetic-read-token"), transport=httpx.MockTransport(handle)
    ) as reader:
        result = reader.resolve(scope, scope.owner_id, "a" * 40)
    assert result.commit_sha == "a" * 40 and result.tree_sha == "b" * 40
    assert result.evidence_origin == "TEST_TRANSPORT"
    assert result.policy_decision == "ALLOW_READ_ONLY_PINNED"
    assert "secret body" not in result.model_dump_json()
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == (
        "https://api.github.com/repos/example/fixture/git/commits/" + "a" * 40
    )
    assert seen[0].headers["authorization"] == "Bearer synthetic-read-token"


@pytest.mark.parametrize(
    "status,code",
    [
        (302, "REDIRECT_DENIED"),
        (401, "AUTH_DENIED"),
        (403, "AUTH_DENIED"),
        (404, "UNAVAILABLE"),
        (429, "RATE_LIMITED"),
        (500, "UNAVAILABLE"),
    ],
)
def test_status_failures_are_safe_and_not_retried(status: int, code: str) -> None:
    scope, calls = grant(), []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status,
            text="secret provider body",
            headers={"location": "https://evil.example/"},
        )

    with GitHubReader(
        SecretStr("synthetic-read-token"), transport=httpx.MockTransport(handle)
    ) as reader:
        with pytest.raises(RepositoryReadError, match=code) as error:
            reader.resolve(scope, scope.owner_id, "a" * 40)
    assert "secret" not in str(error.value)
    assert len(calls) == 1


def test_scope_denial_happens_before_http() -> None:
    scope = grant()

    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("denied scope reached HTTP")

    with GitHubReader(
        SecretStr("synthetic-read-token"), transport=httpx.MockTransport(handle)
    ) as reader:
        for caller, commit in [(uuid4(), "a" * 40), (scope.owner_id, "main")]:
            with pytest.raises(RepositoryReadError, match="SCOPE_DENIED"):
                reader.resolve(scope, caller, commit)


@pytest.mark.parametrize(
    "body,headers,code",
    [
        (b"x" * 65537, {}, "RESPONSE_LIMIT"),
        (b"{}", {"content-encoding": "br"}, "RESPONSE_ENCODING"),
        (b"not JSON", {}, "SCHEMA_INVALID"),
        (b'{"sha":"bad","tree":{}}', {}, "SCHEMA_INVALID"),
        (
            (b'{"sha":"' + b"c" * 40 + b'","tree":{"sha":"' + b"b" * 40 + b'"}}'),
            {},
            "COMMIT_MISMATCH",
        ),
    ],
    ids=["oversize", "compressed", "invalid-json", "invalid-sha", "mismatch"],
)
def test_invalid_provider_response_rejected(body, headers, code) -> None:
    scope = grant()
    with GitHubReader(
        SecretStr("synthetic-read-token"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body, headers=headers)
        ),
    ) as reader:
        with pytest.raises(RepositoryReadError, match=code):
            reader.resolve(scope, scope.owner_id, "a" * 40)


@pytest.mark.parametrize(
    "changes",
    [
        {"consent": False},
        {"private": True, "private_access_approved": False},
    ],
)
def test_missing_consent_never_uses_credentials(changes) -> None:
    scope = grant().model_copy(update=changes)

    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP allowed")

    with GitHubReader(
        SecretStr("synthetic-read-token"), transport=httpx.MockTransport(handle)
    ) as reader:
        with pytest.raises(RepositoryReadError, match="SCOPE_DENIED"):
            reader.resolve(scope, scope.owner_id, "a" * 40)


@pytest.mark.parametrize("token", ["", "bad\nheader", "x" * 4097])
def test_invalid_token_rejected(token: str) -> None:
    with pytest.raises(RepositoryReadError, match="TOKEN_INVALID"):
        GitHubReader(SecretStr(token))


def test_transport_timeout_does_not_expose_request_or_retry() -> None:
    scope, calls = grant(), []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("sensitive provider detail", request=request)

    with GitHubReader(
        SecretStr("synthetic-read-token"), transport=httpx.MockTransport(handle)
    ) as reader:
        with pytest.raises(RepositoryReadError, match="^TIMEOUT$"):
            reader.resolve(scope, scope.owner_id, "a" * 40)
    assert len(calls) == 1


def test_progress_deadline_rejects_delayed_response(monkeypatch) -> None:
    scope = grant()
    ticks = iter([0, 11])
    monkeypatch.setattr(
        "services.repository.github.time.monotonic", lambda: next(ticks)
    )
    with GitHubReader(
        SecretStr("synthetic-read-token"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "sha": "a" * 40,
                    "tree": {"sha": "b" * 40},
                },
            )
        ),
    ) as reader:
        with pytest.raises(RepositoryReadError, match="^TIMEOUT$"):
            reader.resolve(scope, scope.owner_id, "a" * 40)
