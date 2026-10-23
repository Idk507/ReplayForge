"""Explicit GitHub auth configuration; no environment reads or network startup."""

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class GitHubAuthSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    client_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    client_secret: SecretStr = Field(repr=False)
    public_origin: str = Field(max_length=250)
    web_origin: str = Field(max_length=250)
    development: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def valid(self) -> "GitHubAuthSettings":
        secret = self.client_secret.get_secret_value()
        if not 1 <= len(secret) <= 1024 or any(not 33 <= ord(c) <= 126 for c in secret):
            raise ValueError("AUTH_CONFIG_INVALID")
        for origin in (self.public_origin, self.web_origin):
            try:
                url = urlsplit(origin)
                port = url.port
                host = url.hostname or ""
                if (
                    url.username
                    or url.password
                    or url.path
                    or url.query
                    or url.fragment
                    or origin != f"{url.scheme}://{url.netloc}"
                    or "\\" in origin
                    or not re.fullmatch(r"[a-z0-9.-]+", host)
                    or host.startswith(".")
                    or ".." in host
                    or host.endswith(".")
                    or (port is not None and not 1 <= port <= 65535)
                ):
                    raise ValueError
                if self.development:
                    if url.scheme != "http" or host != "127.0.0.1" or port is None:
                        raise ValueError
                elif url.scheme != "https" or host in {"localhost", "127.0.0.1"}:
                    raise ValueError
            except ValueError:
                raise ValueError("AUTH_CONFIG_INVALID") from None
        return self

    @property
    def callback_url(self) -> str:
        return self.public_origin + "/api/v1/auth/github/callback"

    @property
    def success_url(self) -> str:
        return self.web_origin + "/auth/success"

    @property
    def session_cookie(self) -> str:
        return "rf_dev_session" if self.development else "__Host-rf_session"

    @property
    def attempt_cookie(self) -> str:
        return "rf_dev_oauth" if self.development else "__Host-rf_oauth"
