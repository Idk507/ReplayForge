"""Privileged GitHub App token minting; never a browser or model tool.

The caller resolves scope from the current authenticated repository connection.
Minting also requires a fresh durable approval callback, not serialized intent.
No automatic retries, cache, environment discovery or repository writes.
"""

import base64
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Self
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)

from services.approval.journal import PublicationIntent
from services.auth.github import AuthDenied
from services.auth.repositories import Number


class WriteScope(BaseModel):
    """Trusted connection lookup result, not accepted as request authority."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    owner_id: UUID
    repository_id: UUID
    installation_id: Number
    github_repository_id: Number
    full_name: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$"
    )


class InstallationToken(BaseModel):
    """Private short-lived credential; must not cross the privileged boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    token: SecretStr = Field(repr=False)
    expires_at: AwareDatetime
    origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]


class _Repository(BaseModel):
    id: Number
    full_name: str


class _Minted(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    token: SecretStr = Field(min_length=1, max_length=8192, repr=False)
    expires_at: AwareDatetime
    permissions: dict[str, str]
    repositories: tuple[_Repository, ...] = Field(min_length=1, max_length=1)


def _encode(body: bytes) -> str:
    return base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")


class InstallationTokenBroker:
    """One request per admitted mint, confined to the fixed GitHub HTTPS origin.

    GitHub documents RS256, iat clock skew, <=10min JWT expiry and explicit
    repository_ids/permissions reduction. A token is not approval to publish.
    Source: docs.github.com/en/apps/creating-github-apps/authenticating-with-a-
    github-app/generating-an-installation-access-token-for-a-github-app
    """

    def __init__(
        self,
        issuer: str,
        key: rsa.RSAPrivateKey,
        *,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if transport is None and allow_live is not True:
            raise AuthDenied("AUTH_INSTALLATION_LIVE_DENIED")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", issuer)
            or not isinstance(key, rsa.RSAPrivateKey)
            or key.key_size < 2048
        ):
            raise AuthDenied("AUTH_INSTALLATION_CONFIG_INVALID")
        self.origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"] = (
            "TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"
        )
        self._issuer, self._key, self._closed = issuer, key, False
        self._http = httpx.Client(
            base_url="https://api.github.com/",
            transport=transport,
            timeout=httpx.Timeout(5, connect=3),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/vnd.github+json",
                "Accept-Encoding": "identity",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )

    def __enter__(self) -> Self:
        if self._closed:
            raise AuthDenied("AUTH_INSTALLATION_CLOSED")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._closed = True

    def mint(
        self,
        scope: WriteScope,
        authorize: Callable[[], PublicationIntent],
        *,
        now: datetime | None = None,
    ) -> InstallationToken:
        """Recheck exact current approval before POST and before returning token.

        No blind retry after uncertain POST. GitHub may have issued a token even
        if collection fails. It is never returned after failed authorization;
        uncollected credentials expire at the provider (no deletion claim).
        """
        if self._closed:
            raise AuthDenied("AUTH_INSTALLATION_CLOSED")
        scope = WriteScope.model_validate(scope.model_dump())
        instant = datetime.now(UTC) if now is None else now
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise AuthDenied("AUTH_INSTALLATION_CLOCK_INVALID")
        intent = authorize()
        if (
            intent.binding.owner_id != scope.owner_id
            or intent.binding.repository_id != scope.repository_id
        ):
            raise AuthDenied("AUTH_INSTALLATION_SCOPE_DENIED")
        claims = {
            "iat": int(instant.timestamp()) - 60,
            "exp": int(instant.timestamp()) + 540,
            "iss": self._issuer,
        }
        unsigned = ".".join(
            _encode(json.dumps(v, separators=(",", ":")).encode())
            for v in ({"alg": "RS256", "typ": "JWT"}, claims)
        )
        jwt = (
            unsigned
            + "."
            + _encode(
                self._key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
            )
        )
        deadline = time.monotonic() + 10
        try:
            with self._http.stream(
                "POST",
                f"app/installations/{scope.installation_id}/access_tokens",
                headers={"Authorization": "Bearer " + jwt},
                json={
                    "repository_ids": [scope.github_repository_id],
                    "permissions": {"contents": "write", "pull_requests": "write"},
                },
            ) as response:
                if response.status_code != 201:
                    raise AuthDenied("AUTH_INSTALLATION_PROVIDER_DENIED")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise AuthDenied("AUTH_INSTALLATION_RESPONSE_INVALID")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > 65536 or time.monotonic() >= deadline:
                        raise AuthDenied("AUTH_INSTALLATION_RESPONSE_LIMIT")
                    raw.extend(chunk)
                result = _Minted.model_validate_json(raw)
        except (httpx.HTTPError, ValueError, ValidationError):
            raise AuthDenied("AUTH_INSTALLATION_RESPONSE_UNAVAILABLE") from None
        token = result.token.get_secret_value()
        if (
            not all(33 <= ord(c) <= 126 for c in token)
            or result.permissions.get("contents") != "write"
            or result.permissions.get("pull_requests") != "write"
            or set(result.permissions) - {"contents", "pull_requests", "metadata"}
            or result.permissions.get("metadata", "read") != "read"
            or result.repositories[0].id != scope.github_repository_id
            or result.repositories[0].full_name != scope.full_name
            or not instant + timedelta(seconds=30)
            < result.expires_at
            <= instant + timedelta(seconds=3660)
        ):
            raise AuthDenied("AUTH_INSTALLATION_SCOPE_DENIED")
        if authorize() != intent:
            raise AuthDenied("AUTH_INSTALLATION_APPROVAL_CHANGED")
        return InstallationToken(
            token=result.token, expires_at=result.expires_at, origin=self.origin
        )
