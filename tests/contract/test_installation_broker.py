"""Reduced installation token minting with synthetic keys and HTTP only."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services.approval.journal import PublicationIntent
from services.approval.policy import ApprovalBinding
from services.auth.github import AuthDenied
from services.auth.installation import InstallationTokenBroker, WriteScope


def setup(fault=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC).replace(microsecond=0)
    scope = WriteScope(
        owner_id=uuid4(),
        repository_id=uuid4(),
        installation_id=7,
        github_repository_id=42,
        full_name="owner/demo",
    )
    intent = PublicationIntent(
        external_id=uuid4(),
        approval_nonce=uuid4(),
        reviewer_id=uuid4(),
        binding=ApprovalBinding(
            run_id=uuid4(),
            repository_id=scope.repository_id,
            owner_id=scope.owner_id,
            source_commit="a" * 40,
            patch_ref="sha256:" + "b" * 64,
            verification_ref="sha256:" + "c" * 64,
            target_branch="main",
            policy_version="repository-v1",
        ),
        head_branch="replayforge/" + "a" * 32 + "-" + "b" * 12,
    )
    calls = []

    def handle(request):
        calls.append(request)
        response = {
            "token": "synthetic-token-do-not-log",
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
                "metadata": "read",
            },
            "repositories": [{"id": 42, "full_name": "owner/demo"}],
        }
        if fault == "broad":
            response["permissions"]["administration"] = "write"
        if fault == "wrong_repo":
            response["repositories"][0]["id"] = 43
        if fault == "expired":
            response["expires_at"] = now.isoformat()
        if fault == "missing_repo":
            del response["repositories"]
        if fault == "invalid_token":
            response["token"] = "line\nbreak"
        if fault == "rate":
            return httpx.Response(429)
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.example/"})
        if fault == "drop":
            raise httpx.ReadTimeout("private provider detail", request=request)
        return httpx.Response(201, json=response)

    return key, now, scope, intent, calls, httpx.MockTransport(handle)


def test_token_request_is_repository_and_permission_reduced_and_jwt_signed():
    key, now, scope, intent, calls, transport = setup()
    with InstallationTokenBroker("Iv1.demo", key, transport=transport) as broker:
        token = broker.mint(scope, lambda: intent, now=now)
    assert token.token.get_secret_value() == "synthetic-token-do-not-log"
    assert token.origin == "TEST_TRANSPORT"
    assert "synthetic-token" not in repr(token)
    request = calls[0]
    assert request.url == "https://api.github.com/app/installations/7/access_tokens"
    assert json.loads(request.content) == {
        "repository_ids": [42],
        "permissions": {"contents": "write", "pull_requests": "write"},
    }
    jwt = request.headers["authorization"].removeprefix("Bearer ")
    header, payload, signature = jwt.split(".")

    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    assert json.loads(decode(header))["alg"] == "RS256"
    claims = json.loads(decode(payload))
    assert claims == {
        "iat": int(now.timestamp()) - 60,
        "exp": int(now.timestamp()) + 540,
        "iss": "Iv1.demo",
    }
    key.public_key().verify(
        decode(signature),
        f"{header}.{payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


@pytest.mark.parametrize(
    "fault",
    [
        "broad",
        "wrong_repo",
        "expired",
        "missing_repo",
        "invalid_token",
        "rate",
        "redirect",
        "drop",
    ],
)
def test_bad_provider_result_never_returns_a_credential_or_retries(fault):
    key, now, scope, intent, calls, transport = setup(fault)
    with InstallationTokenBroker("Iv1.demo", key, transport=transport) as broker:
        with pytest.raises(AuthDenied, match="AUTH_INSTALLATION_"):
            broker.mint(scope, lambda: intent, now=now)
    assert len(calls) == 1


def test_wrong_intent_or_lost_permission_prevents_mint():
    key, now, scope, intent, calls, transport = setup()
    changed = intent.model_copy(
        update={"binding": intent.binding.model_copy(update={"owner_id": uuid4()})}
    )
    with InstallationTokenBroker("Iv1.demo", key, transport=transport) as broker:
        with pytest.raises(AuthDenied):
            broker.mint(scope, lambda: changed, now=now)
    assert calls == []


def test_permission_is_rechecked_after_mint_and_changed_intent_is_denied():
    key, now, scope, intent, calls, transport = setup()
    answers = iter([intent, intent.model_copy(update={"external_id": uuid4()})])
    with InstallationTokenBroker("Iv1.demo", key, transport=transport) as broker:
        with pytest.raises(AuthDenied):
            broker.mint(scope, lambda: next(answers), now=now)
    assert len(calls) == 1


def test_live_mint_requires_explicit_opt_in():
    key, _, _, _, _, _ = setup()
    with pytest.raises(AuthDenied, match="AUTH_INSTALLATION_LIVE_DENIED"):
        InstallationTokenBroker("Iv1.demo", key)
