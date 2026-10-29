from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.runner.import_diagnostics import capture_sdk_import_error

REQUEST_HASH = "a" * 64


class ApiStatusCodeError(Exception):
    def __init__(self, *, status: int, url: str, error: str, headers: dict[str, str]):
        self.status = status
        self.error = error
        self.request = SimpleNamespace(method="POST", url=url)
        self.response = SimpleNamespace(headers=headers)


def test_submit_error_records_bounded_provider_metadata_without_raw_secrets() -> None:
    error = ApiStatusCodeError(
        status=404,
        url="https://api.tokenfactory.nebius.com/sandboxes/v1/images/import?token=hidden",
        error="registry pass=hidden and bearer=secret",
        headers={"X-Request-ID": "req-123", "Set-Cookie": "session=hidden"},
    )

    record = capture_sdk_import_error(
        error,
        request_sha256=REQUEST_HASH,
        phase="submit",
        sdk_version="0.3.6",
    )

    encoded = record.model_dump_json()
    assert record.outcome == "PROVIDER_REJECTED"
    assert record.method == "POST"
    assert record.path == "/sandboxes/v1/images/import"
    assert record.http_status == 404
    assert record.request_id == "req-123"
    assert record.operation_id is None
    assert record.retryable is False
    assert "hidden" not in encoded and "secret" not in encoded
    assert "registry" not in encoded and "Set-Cookie" not in encoded


@pytest.mark.parametrize("status", [None, 408, 429, 500, 503])
def test_submit_uncertain_or_transient_response_never_authorizes_retry(
    status: int | None,
) -> None:
    error = ApiStatusCodeError(
        status=status or 0,
        url="https://api.tokenfactory.nebius.com/v1/images/import",
        error="sensitive provider body",
        headers={},
    )
    if status is None:
        error.status = None

    record = capture_sdk_import_error(
        error,
        request_sha256=REQUEST_HASH,
        phase="submit",
        sdk_version="0.3.6",
    )

    assert record.outcome == "SUBMISSION_UNKNOWN"
    assert record.retryable is False


def test_poll_error_preserves_known_operation_and_only_safe_endpoint_path() -> None:
    operation = uuid4()
    error = SimpleNamespace(
        request=SimpleNamespace(
            method="GET",
            url=f"https://api.tokenfactory.nebius.com/sandboxes/v1/operations/{operation}?key=secret",
        ),
        response=SimpleNamespace(headers={}),
        status=502,
    )

    record = capture_sdk_import_error(
        error,
        request_sha256=REQUEST_HASH,
        phase="poll",
        operation_id=operation,
        sdk_version="0.3.6",
    )

    assert record.outcome == "POLL_ERROR"
    assert record.method == "GET"
    assert record.path == f"/sandboxes/v1/operations/{operation}"
    assert record.operation_id == operation
    assert "secret" not in record.model_dump_json()


def test_invalid_request_hash_is_rejected() -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        capture_sdk_import_error(
            RuntimeError("do not serialize me"),
            request_sha256="not-a-hash",
            phase="submit",
        )
