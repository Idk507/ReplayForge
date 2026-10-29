"""Secret-safe diagnostics for one-shot Nebius OCI image imports.

This module only normalizes retained SDK exception metadata. It never submits,
retries, polls, or decides whether an uncertain import may be repeated.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_IMPORT_PATH = "/sandboxes/v1/images/import"
_ALLOWED_PATHS = {_IMPORT_PATH}
_Outcome = Literal["SUBMISSION_UNKNOWN", "PROVIDER_REJECTED", "POLL_ERROR"]
_ErrorClass = Literal[
    "ApiStatusCodeError",
    "ApiTimeoutError",
    "ContreeTransportError",
    "FailedOperationError",
    "OperationTimedOutError",
    "UnknownProviderError",
]


class ImportDiagnostic(BaseModel):
    """Bounded import failure context safe for an operator evidence record."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["1.0"] = "1.0"
    outcome: Literal["SUBMISSION_UNKNOWN", "PROVIDER_REJECTED", "POLL_ERROR"]
    observed_at: AwareDatetime
    method: Literal["POST", "GET"]
    path: str = Field(min_length=1, max_length=96)
    http_status: int | None = Field(default=None, ge=100, le=599)
    request_id: str | None = Field(default=None, max_length=128)
    operation_id: UUID | None = None
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sdk_version: str = Field(min_length=1, max_length=32)
    error_class: _ErrorClass
    retryable: Literal[False] = False


def capture_sdk_import_error(
    error: BaseException,
    *,
    request_sha256: str,
    phase: Literal["submit", "poll"],
    operation_id: UUID | None = None,
    observed_at: datetime | None = None,
    sdk_version: str | None = None,
) -> ImportDiagnostic:
    """Extract only allowlisted metadata; raw SDK messages are never retained.

    Submission errors lacking a provider operation ID remain explicitly
    unknown. A known operation error remains tied to that ID. This function
    intentionally does not make a retry decision.
    """
    if not _SHA256.fullmatch(request_sha256):
        raise ValueError("request_sha256 must be lowercase SHA-256 hex")

    expected_method = "POST" if phase == "submit" else "GET"
    request = getattr(error, "request", None)
    method = getattr(request, "method", None)
    method = method.upper() if isinstance(method, str) else expected_method
    if method != expected_method:
        method = expected_method

    operation_id = operation_id or getattr(error, "operation_uuid", None)
    if operation_id is not None and not isinstance(operation_id, UUID):
        try:
            operation_id = UUID(str(operation_id))
        except (ValueError, TypeError, AttributeError):
            operation_id = None

    path = _IMPORT_PATH
    if phase == "poll" and operation_id is not None:
        path = f"/sandboxes/v1/operations/{operation_id}"
    request_url = getattr(request, "url", None)
    if isinstance(request_url, str):
        parsed = urlsplit(request_url)
        if parsed.hostname == "api.tokenfactory.nebius.com":
            if parsed.path in _ALLOWED_PATHS:
                path = parsed.path
            elif phase == "poll" and operation_id is not None and parsed.path == path:
                path = parsed.path

    status = getattr(error, "status", None)
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not 100 <= status <= 599
    ):
        status = None

    request_id: str | None = None
    if isinstance(headers, dict):
        for key, value in headers.items():
            if (
                isinstance(key, str)
                and key.lower()
                in {
                    "x-request-id",
                    "x-nebius-request-id",
                    "x-trace-id",
                }
                and isinstance(value, str)
                and _SAFE_ID.fullmatch(value)
            ):
                request_id = value
                break

    safe_classes = {
        "ApiStatusCodeError",
        "ApiTimeoutError",
        "ContreeTransportError",
        "FailedOperationError",
        "OperationTimedOutError",
    }
    class_name = type(error).__name__
    if class_name == "RunnerError":
        # RunnerError values are stable internal codes; admit only known codes.
        code_to_class = {
            "S_TIMEOUT": "ApiTimeoutError",
            "S_UNAVAILABLE": "ContreeTransportError",
        }
        class_name = code_to_class.get(str(error), class_name)
    error_class = cast(
        _ErrorClass,
        class_name if class_name in safe_classes else "UnknownProviderError",
    )
    if phase == "poll":
        outcome: _Outcome = "POLL_ERROR"
    elif status is not None and 400 <= status < 500 and status not in {408, 429}:
        outcome = "PROVIDER_REJECTED"
    else:
        outcome = "SUBMISSION_UNKNOWN"

    try:
        resolved_sdk_version = sdk_version or version("contree-sdk")
    except PackageNotFoundError:
        resolved_sdk_version = sdk_version or "unknown"

    return ImportDiagnostic(
        outcome=outcome,
        observed_at=observed_at or datetime.now(UTC),
        method=cast(Literal["POST", "GET"], expected_method),
        path=path,
        http_status=status,
        request_id=request_id,
        operation_id=operation_id,
        request_sha256=request_sha256,
        sdk_version=resolved_sdk_version,
        error_class=error_class,
    )
