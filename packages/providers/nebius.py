"""Bounded M0 connectivity probe, not the worker's production inference gateway.

Only a fixed synthetic prompt leaves this client. Responses and provider errors
are never logged. Automatic retries are deliberately disabled for this probe.
"""

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

DEFAULT_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
ALLOWED_HOSTS = frozenset(
    {"api.tokenfactory.us-central1.nebius.com", "api.tokenfactory.nebius.com"}
)
MAX_RESPONSE_BYTES = 1_000_000


class ProbeError(Exception):
    """Stable public error code without provider payload or credentials."""


class NebiusConfig(BaseModel):
    """Operator configuration; construction goes through from_env."""

    model_config = ConfigDict(frozen=True)
    api_key: SecretStr
    base_url: str
    model: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "NebiusConfig":
        """Read credentials only from environment; deny arbitrary destinations."""
        values = os.environ if env is None else env
        key = values.get("NEBIUS_API_KEY", "").strip()
        if not key:
            raise ProbeError("CONFIG_MISSING_KEY")
        if not key.isascii() or any(c.isspace() or ord(c) < 32 for c in key):
            raise ProbeError("CONFIG_INVALID")
        base_url = values.get("NEBIUS_BASE_URL", DEFAULT_URL)
        model = values.get("NEBIUS_MODEL_PRIMARY", DEFAULT_MODEL)
        try:
            url = urlsplit(base_url)
            valid_url = (
                url.scheme == "https"
                and url.hostname in ALLOWED_HOSTS
                and url.port in (None, 443)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and url.path in ("/v1", "/v1/")
            )
        except ValueError:
            raise ProbeError("CONFIG_INVALID") from None
        if not valid_url or not re.fullmatch(
            r"nvidia/[A-Za-z0-9_.-]*[Nn]emotron[A-Za-z0-9_.-]*", model
        ):
            raise ProbeError("CONFIG_INVALID")
        return cls(
            api_key=SecretStr(key), base_url=base_url.rstrip("/") + "/", model=model
        )


class CatalogItem(BaseModel):
    """Minimal catalog contract; other provider metadata is intentionally ignored."""

    id: str


class Catalog(BaseModel):
    """OpenAI-compatible model catalog."""

    data: list[CatalogItem]


class Message(BaseModel):
    """Only final text is needed to validate this synthetic smoke call."""

    content: str = Field(min_length=1)


class Choice(BaseModel):
    """Completion status is required to reject truncation."""

    finish_reason: str
    message: Message


class Usage(BaseModel):
    """Missing usage remains unknown, never zero-filled."""

    prompt_tokens: int = Field(ge=0, strict=True)
    completion_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)


class Completion(BaseModel):
    """Validated minimal response envelope."""

    model: str
    choices: list[Choice] = Field(min_length=1, max_length=1)
    usage: Usage | None = None


class ProbeResult(BaseModel):
    """Safe observed metadata, without prompt, response text or provider IDs."""

    schema_version: str = "1.0"
    evidence_origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    trace_id: str
    observed_at: datetime
    model: str
    endpoint: str
    http_status: int
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None = None


def _request(client: httpx.Client, method: str, path: str) -> bytes:
    """Limit response bytes and sanitize errors before crossing the boundary."""
    payload = None
    if method == "POST":
        payload = {
            "model": client.headers["x-replayforge-model"],
            "messages": [{"role": "user", "content": "Reply with the word OK."}],
            "max_tokens": 128,
            "temperature": 0,
            "stream": False,
        }
    try:
        with client.stream(method, path, json=payload) as response:
            status = response.status_code
            if 300 <= status < 400:
                raise ProbeError("M_REDIRECT_DENIED")
            if status in (401, 403):
                raise ProbeError("M_AUTH")
            if status == 429:
                raise ProbeError("M_RATE_LIMIT")
            if status != 200:
                raise ProbeError("M_UNAVAILABLE")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ProbeError("M_RESPONSE_TOO_LARGE")
            return bytes(body)
    except httpx.TimeoutException:
        raise ProbeError("M_TIMEOUT") from None
    except httpx.HTTPError:
        raise ProbeError("M_UNAVAILABLE") from None


def probe(
    config: NebiusConfig, *, transport: httpx.BaseTransport | None = None
) -> ProbeResult:
    """Check catalog membership, then issue at most one synthetic completion.

    Injected transports are for offline tests only. Each request has bounded
    connect/read/write/pool timeouts and proxy/environment trust is disabled.
    """
    # Revalidate even caller-constructed configuration before attaching a key.
    config = NebiusConfig.from_env(
        {
            "NEBIUS_API_KEY": config.api_key.get_secret_value(),
            "NEBIUS_BASE_URL": config.base_url,
            "NEBIUS_MODEL_PRIMARY": config.model,
        }
    )
    start = monotonic()
    with httpx.Client(
        base_url=config.base_url,
        headers={
            "Authorization": f"Bearer {config.api_key.get_secret_value()}",
            "x-replayforge-model": config.model,
        },
        timeout=httpx.Timeout(30, connect=10),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        try:
            catalog = Catalog.model_validate_json(_request(client, "GET", "models"))
            if config.model not in {item.id for item in catalog.data}:
                raise ProbeError("M_MODEL_UNAVAILABLE")
            completion = Completion.model_validate_json(
                _request(client, "POST", "chat/completions")
            )
        except ValidationError:
            raise ProbeError("M_SCHEMA_INVALID") from None
        if (
            completion.model != config.model
            or completion.choices[0].finish_reason != "stop"
            or not completion.choices[0].message.content.strip()
        ):
            raise ProbeError("M_SCHEMA_INVALID")
    usage = completion.usage
    return ProbeResult(
        evidence_origin="TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE",
        trace_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        model=config.model,
        endpoint=config.base_url,
        http_status=200,
        latency_ms=round((monotonic() - start) * 1000, 3),
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
    )
