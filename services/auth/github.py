"""Bounded GitHub App OAuth HTTP port, separate from installation credentials."""

import json
import re
import time
from typing import Literal, Self, TypeVar
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from services.auth.settings import GitHubAuthSettings


class AuthDenied(Exception):
    """Sanitized stable auth failure; never include provider messages or secrets."""


class PrivateModel(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)


class UserToken(PrivateModel):
    access_token: SecretStr = Field(min_length=1, max_length=8192, repr=False)
    refresh_token: SecretStr = Field(min_length=1, max_length=8192, repr=False)
    token_type: Literal["bearer"]
    expires_in: int = Field(strict=True, ge=1, le=28800)
    refresh_token_expires_in: int = Field(strict=True, ge=1, le=15897600)


class GitHubUser(PrivateModel):
    id: int = Field(strict=True, ge=1, le=9223372036854775807)
    login: str = Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
    avatar_url: str = Field(
        pattern=r"^https://avatars\.githubusercontent\.com/[A-Za-z0-9/?=&_.%-]{1,200}$"
    )


T = TypeVar("T", bound=BaseModel)


class GitHubAuthClient:
    """One request per call; caller owns durable single-use exchange semantics.

    No secret discovery, startup I/O, redirects, proxy inheritance or automatic
    retry. Custom transports are test provenance; real I/O is explicit opt-in.
    """

    def __init__(
        self,
        settings: GitHubAuthSettings,
        *,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = GitHubAuthSettings.model_validate(settings.model_dump())
        self.origin: Literal["REMOTE_SERVICE", "TEST_TRANSPORT"] = (
            "REMOTE_SERVICE" if transport is None else "TEST_TRANSPORT"
        )
        self._live = allow_live is True
        self._closed = False
        self._client = httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5, connect=3),
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
        self._closed = True

    def authorize_url(self, state: str, challenge: str) -> str:
        if not all(re.fullmatch(r"[A-Za-z0-9_-]{43}", s) for s in (state, challenge)):
            raise AuthDenied("AUTH_INPUT_INVALID")
        return "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.callback_url,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "offline_access",
            }
        )

    def _request(
        self,
        model: type[T],
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        token: SecretStr | None = None,
        max_bytes: int = 65536,
    ) -> T:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 524288:
            raise AuthDenied("AUTH_INPUT_INVALID")
        if self._closed or (self.origin == "REMOTE_SERVICE" and not self._live):
            raise AuthDenied("AUTH_LIVE_NOT_AUTHORIZED")
        headers = (
            {}
            if token is None
            else {"Authorization": "Bearer " + token.get_secret_value()}
        )
        deadline = time.monotonic() + 10
        try:
            with self._client.stream(method, url, data=data, headers=headers) as r:
                if r.status_code != 200:
                    raise AuthDenied(
                        "AUTH_RATE_LIMITED"
                        if r.status_code == 429
                        else "AUTH_PROVIDER_DENIED"
                        if r.status_code in {401, 403}
                        else "AUTH_PROVIDER_FAILED"
                    )
                if r.headers.get("content-encoding", "identity") != "identity":
                    raise AuthDenied("AUTH_RESPONSE_INVALID")
                raw = bytearray()
                for chunk in r.iter_bytes():
                    if (
                        len(raw) + len(chunk) > max_bytes
                        or time.monotonic() >= deadline
                    ):
                        raise AuthDenied("AUTH_RESPONSE_LIMIT")
                    raw.extend(chunk)
                return model.model_validate(json.loads(raw))
        except (httpx.HTTPError, ValueError, ValidationError):
            raise AuthDenied("AUTH_PROVIDER_INVALID_OR_UNAVAILABLE") from None

    def exchange(self, code: SecretStr, verifier: SecretStr) -> UserToken:
        code_value, verifier_value = (
            code.get_secret_value(),
            verifier.get_secret_value(),
        )
        if (
            not 1 <= len(code_value) <= 512
            or any(not 33 <= ord(c) <= 126 for c in code_value)
            or not re.fullmatch(r"[A-Za-z0-9_~-]{43,128}", verifier_value)
        ):
            raise AuthDenied("AUTH_INPUT_INVALID")
        return self._request(
            UserToken,
            "POST",
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret.get_secret_value(),
                "code": code_value,
                "code_verifier": verifier_value,
                "redirect_uri": self.settings.callback_url,
            },
        )

    def user(self, token: SecretStr) -> GitHubUser:
        value = token.get_secret_value()
        if not 1 <= len(value) <= 8192 or any(not 33 <= ord(c) <= 126 for c in value):
            raise AuthDenied("AUTH_INPUT_INVALID")
        return self._request(
            GitHubUser, "GET", "https://api.github.com/user", token=token
        )
