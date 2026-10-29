"""One-shot Nebius image import transport with durable callback boundaries.

Live use is opt-in. This adapter deliberately avoids the contree-sdk high-level
``images.import_from`` retry/poll wrapper so the provider operation is retained
before any subsequent request and an uncertain POST is never repeated.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from packages.contracts.sandbox import RunnerError
from services.runner.import_diagnostics import (
    ImportDiagnostic,
    capture_sdk_import_error,
)

IMPORT_BASE_URL = "https://api.tokenfactory.nebius.com/sandboxes/"
IMPORT_PATH = "v1/images/import"
MAX_RESPONSE_BYTES = 16 * 1024
MAX_POLLS = 20
MAX_WAIT_SECONDS = 300
_OCI_DIGEST_REF = re.compile(
    r"^docker://[a-z0-9][a-z0-9.-]{0,252}/[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*@sha256:[0-9a-f]{64}$"
)
_PROJECT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def validate_import_polling(
    max_polls: int, timeout_seconds: int, poll_interval_seconds: float
) -> None:
    """Reject invalid polling policy before an intent or provider action."""
    if (
        type(max_polls) is not int
        or not 1 <= max_polls <= MAX_POLLS
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= MAX_WAIT_SECONDS
        or type(poll_interval_seconds) not in (int, float)
        or not math.isfinite(poll_interval_seconds)
        or not 0 <= poll_interval_seconds <= 5
    ):
        raise RunnerError("S_POLICY_DENIED")


class ImageImportConfig(BaseModel):
    """IAM secret and project context; provider origin is fixed by code."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    api_key: SecretStr
    project_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ImageImportConfig:
        """Resolve only the two required values without logging their contents."""
        api_key, project_id = env.get("NEBIUS_API_KEY"), env.get("NEBIUS_PROJECT_ID")
        if not api_key or not project_id:
            raise RunnerError("S_CONFIG_MISSING")
        if not _PROJECT_ID.fullmatch(project_id) or not re.fullmatch(
            r"[\x21-\x7e]{1,4096}", api_key
        ):
            raise RunnerError("S_CONFIG_INVALID")
        return cls(api_key=SecretStr(api_key), project_id=project_id)


class ImageImportPlan(BaseModel):
    """Immutable public OCI digest request; omitting tag creates private output."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    source: str = Field(min_length=32, max_length=512)
    timeout_seconds: int = Field(default=120, ge=1, le=300)

    def request_body(self) -> dict[str, object]:
        """Return API body without a shared tag or registry credentials."""
        parsed = urlsplit(self.source)
        if (
            not _OCI_DIGEST_REF.fullmatch(self.source)
            or parsed.scheme != "docker"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise RunnerError("S_IMAGE_REFERENCE_INVALID")
        return {
            "registry": {"url": self.source},
            "timeout": self.timeout_seconds,
        }

    def request_digest(self) -> str:
        """Hash the exact bounded request body; never include IAM credentials."""
        body = self.request_body()
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class ImageImportAccepted(BaseModel):
    """Provider operation identity captured directly from the 201 response."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    operation_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_at: AwareDatetime


class ImageImportResult(BaseModel):
    """Provider image UUID remains distinct from its source OCI digest."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    operation_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["SUCCESS", "FAILED", "CANCELLED"]
    image_uuid: UUID | None = None
    observed_at: AwareDatetime
    failure_code: Literal["REGISTRY_HTTP_ERROR", "IMPORT_OPERATION_FAILED"] | None = (
        None
    )
    provider_http_status: int | None = Field(default=None, ge=400, le=599)
    error_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ImageImportCallError(Exception):
    """Safe failure with a diagnostic record; contains no provider response body."""

    def __init__(self, diagnostic: ImportDiagnostic) -> None:
        super().__init__(diagnostic.outcome)
        self.diagnostic = diagnostic


class ImageImportLifecycle(Protocol):
    """Project/request-bound durable callbacks required by the transport."""

    @property
    def project_id(self) -> str: ...

    @property
    def request_digest(self) -> str: ...

    @property
    def operation_id(self) -> UUID | None: ...

    @property
    def state(self) -> str | None: ...

    def claim(self, request_digest: str) -> bool: ...

    def operation(self, operation_id: UUID, request_digest: str) -> None: ...

    def diagnostic(self, record: ImportDiagnostic) -> None: ...


class _ResponseFailure(Exception):
    """Local carrier for allowlisted HTTP response metadata only."""

    def __init__(self, status: int, path: str, headers: Mapping[str, str]) -> None:
        self.status = status
        self.request = type(
            "Request",
            (),
            {
                "method": "GET" if path.startswith("v1/operations") else "POST",
                "url": IMPORT_BASE_URL + path,
            },
        )()
        self.response = type("Response", (), {"headers": dict(headers)})()


class OneShotNebiusImageImporter:
    """One POST per instance; retain provider UUID before bounded GET polling."""

    def __init__(
        self,
        config: ImageImportConfig,
        *,
        allow_live: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        known_operation: UUID | None = None,
        known_request_hash: str | None = None,
    ) -> None:
        if transport is None and allow_live is not True:
            raise RunnerError("S_LIVE_NOT_AUTHORIZED")
        if (known_operation is None) != (known_request_hash is None):
            raise RunnerError("S_OPERATION_NOT_OWNED")
        if known_request_hash is not None and not re.fullmatch(
            r"[0-9a-f]{64}", known_request_hash
        ):
            raise RunnerError("S_OPERATION_NOT_OWNED")
        self._client = httpx.AsyncClient(
            base_url=IMPORT_BASE_URL,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Project": config.project_id,
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(10, connect=3),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._project_id = config.project_id
        self._submitted = known_operation is not None
        self._operation_id = known_operation
        self._request_hash = known_request_hash
        self._closed = False

    async def __aenter__(self) -> OneShotNebiusImageImporter:
        if self._closed:
            raise RunnerError("S_CLOSED")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()
        self._closed = True

    @staticmethod
    def _retain_diagnostic(
        callback: Callable[[ImportDiagnostic], None], diagnostic: ImportDiagnostic
    ) -> None:
        try:
            callback(diagnostic)
        except Exception:
            raise RunnerError("S_DIAGNOSTIC_RETENTION_FAILED") from None

    async def _request_json(
        self, method: Literal["POST", "GET"], path: str, body: dict[str, object] | None
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        try:
            async with self._client.stream(method, path, json=body) as response:
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise _ResponseFailure(
                        response.status_code, path, dict(response.headers)
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise _ResponseFailure(
                            response.status_code, path, dict(response.headers)
                        )
                    raw.extend(chunk)
                try:
                    data: object = json.loads(raw) if raw else {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise _ResponseFailure(
                        response.status_code, path, dict(response.headers)
                    ) from None
                if not isinstance(data, dict):
                    raise _ResponseFailure(
                        response.status_code, path, dict(response.headers)
                    )
                return response.status_code, dict(response.headers), data
        except httpx.TimeoutException:
            raise RunnerError("S_TIMEOUT") from None
        except httpx.HTTPError:
            raise RunnerError("S_UNAVAILABLE") from None

    async def submit(
        self,
        plan: ImageImportPlan,
        *,
        journal: ImageImportLifecycle,
    ) -> ImageImportAccepted:
        """Claim durable intent before one POST; retain its UUID immediately."""
        if self._closed or self._submitted:
            raise RunnerError("S_CLOSED_OR_ALREADY_SUBMITTED")
        body = plan.request_body()
        request_hash = plan.request_digest()
        if (
            journal.project_id != self._project_id
            or journal.request_digest != request_hash
            or journal.state is not None
        ):
            raise RunnerError("S_IMPORT_JOURNAL_BINDING")
        if journal.claim(request_hash) is not True:
            raise RunnerError("S_MATERIALIZATION_ALREADY_CLAIMED")
        self._submitted = True
        self._request_hash = request_hash
        try:
            status, headers, data = await self._request_json("POST", IMPORT_PATH, body)
        except (RunnerError, _ResponseFailure) as error:
            diagnostic = capture_sdk_import_error(
                error,
                request_sha256=request_hash,
                phase="submit",
            )
            self._retain_diagnostic(journal.diagnostic, diagnostic)
            raise ImageImportCallError(diagnostic) from None
        if status != 201:
            diagnostic = capture_sdk_import_error(
                _ResponseFailure(status, IMPORT_PATH, headers),
                request_sha256=request_hash,
                phase="submit",
            )
            self._retain_diagnostic(journal.diagnostic, diagnostic)
            raise ImageImportCallError(diagnostic)
        try:
            operation_id = UUID(str(data["uuid"]))
        except (KeyError, ValueError, TypeError):
            diagnostic = capture_sdk_import_error(
                _ResponseFailure(status, IMPORT_PATH, headers),
                request_sha256=request_hash,
                phase="submit",
            )
            self._retain_diagnostic(journal.diagnostic, diagnostic)
            raise ImageImportCallError(diagnostic) from None
        self._operation_id = operation_id
        # This must be the first action after decoding the 201 operation UUID.
        try:
            journal.operation(operation_id, request_hash)
        except Exception:
            # Keep the in-memory one-shot guard. The caller must reconcile this
            # accepted operation; a failed journal write never permits resubmit.
            raise RunnerError("S_OPERATION_RETENTION_FAILED") from None
        return ImageImportAccepted(
            operation_id=operation_id,
            request_sha256=request_hash,
            accepted_at=datetime.now(UTC),
        )

    async def wait(
        self,
        operation_id: UUID,
        *,
        journal: ImageImportLifecycle,
        max_polls: int = MAX_POLLS,
        timeout_seconds: int = MAX_WAIT_SECONDS,
        poll_interval_seconds: float = 1.0,
    ) -> ImageImportResult:
        """Perform bounded GET-only polling for the already retained operation."""
        if self._closed or operation_id != self._operation_id:
            raise RunnerError("S_OPERATION_NOT_OWNED")
        if (
            journal.project_id != self._project_id
            or journal.request_digest != self._request_hash
            or journal.operation_id != operation_id
            or journal.state != "OPERATION_KNOWN"
        ):
            raise RunnerError("S_IMPORT_JOURNAL_BINDING")
        validate_import_polling(max_polls, timeout_seconds, poll_interval_seconds)
        deadline = time.monotonic() + timeout_seconds
        for poll_number in range(max_polls):
            if time.monotonic() >= deadline:
                break
            try:
                status, headers, data = await self._request_json(
                    "GET", f"v1/operations/{operation_id}", None
                )
            except (RunnerError, _ResponseFailure) as error:
                diagnostic = capture_sdk_import_error(
                    error,
                    request_sha256=self._request_hash or ("0" * 64),
                    phase="poll",
                    operation_id=operation_id,
                )
                self._retain_diagnostic(journal.diagnostic, diagnostic)
                raise ImageImportCallError(diagnostic) from None
            if status != 200:
                diagnostic = capture_sdk_import_error(
                    _ResponseFailure(status, f"v1/operations/{operation_id}", headers),
                    request_sha256=self._request_hash or ("0" * 64),
                    phase="poll",
                    operation_id=operation_id,
                )
                self._retain_diagnostic(journal.diagnostic, diagnostic)
                raise ImageImportCallError(diagnostic)
            if (
                data.get("uuid") != str(operation_id)
                or data.get("kind") != "image_import"
            ):
                raise RunnerError("S_CORRELATION_FAILED")
            state = data.get("status")
            if state in {"SUCCESS", "FAILED", "CANCELLED"}:
                result = data.get("result")
                image_uuid = None
                if isinstance(result, dict) and result.get("image") is not None:
                    try:
                        image_uuid = UUID(str(result["image"]))
                    except (ValueError, TypeError):
                        raise RunnerError("S_SCHEMA_INVALID") from None
                if state == "SUCCESS" and image_uuid is None:
                    raise RunnerError("S_SCHEMA_INVALID")
                failure_code: (
                    Literal["REGISTRY_HTTP_ERROR", "IMPORT_OPERATION_FAILED"] | None
                ) = None
                provider_http_status = None
                error_sha256 = None
                raw_error = data.get("error")
                if state != "SUCCESS" and isinstance(raw_error, str) and raw_error:
                    error_sha256 = hashlib.sha256(raw_error.encode()).hexdigest()
                    failure_code = "IMPORT_OPERATION_FAILED"
                    match = re.search(r"\bHTTP\s+([45][0-9]{2})\b", raw_error)
                    if match is not None:
                        provider_http_status = int(match.group(1))
                        if "registry" in raw_error.lower():
                            failure_code = "REGISTRY_HTTP_ERROR"
                return ImageImportResult(
                    operation_id=operation_id,
                    request_sha256=self._request_hash or ("0" * 64),
                    status=cast(Literal["SUCCESS", "FAILED", "CANCELLED"], state),
                    image_uuid=image_uuid,
                    observed_at=datetime.now(UTC),
                    failure_code=failure_code,
                    provider_http_status=provider_http_status,
                    error_sha256=error_sha256,
                )
            if state not in {"ASSIGNED", "PENDING", "EXECUTING"}:
                raise RunnerError("S_SCHEMA_INVALID")
            if poll_number + 1 < max_polls and time.monotonic() < deadline:
                await asyncio.sleep(
                    min(poll_interval_seconds, max(deadline - time.monotonic(), 0))
                )
        raise RunnerError("S_MATERIALIZATION_PENDING")
