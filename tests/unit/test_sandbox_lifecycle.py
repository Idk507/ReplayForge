"""Adversarial lifecycle cases using explicitly fake, offline observations."""

import json
from uuid import UUID

import httpx
import pytest

from packages.contracts.sandbox import RunnerError
from services.runner.remote import NebiusSandboxBackend, SandboxConfig
from services.runner.smoke import run_smoke

IMAGE = "11111111-1111-4111-8111-111111111111"
OPERATION = "22222222-2222-4222-8222-222222222222"


def config() -> SandboxConfig:
    return SandboxConfig.from_env(
        {
            "NEBIUS_API_KEY": "test-key",
            "NEBIUS_PROJECT_ID": "project-test",
            "SANDBOX_IMAGE_UUID": IMAGE,
        }
    )


def test_poll_count_bounds_pending_operation_and_cleanup_wait() -> None:
    counts = {"POST": 0, "GET": 0, "DELETE": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        counts[request.method] += 1
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": OPERATION})
        if request.method == "DELETE":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "uuid": OPERATION,
                "kind": "instance",
                "status": "PENDING",
                "image_uuid": IMAGE,
                "result_image_uuid": None,
            },
        )

    report = run_smoke(
        config(),
        allow_live=True,
        transport=httpx.MockTransport(respond),
        sleep=lambda seconds: None,
    )
    assert report.error_class == report.cleanup_error_class == "S_TIMEOUT"
    assert not report.compute_terminal_confirmed
    assert counts == {"POST": 1, "GET": 25, "DELETE": 1}


def test_cancellation_conflict_still_inspects_terminal_operation() -> None:
    methods = []

    def respond(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": OPERATION})
        if methods == ["POST", "GET"]:
            return httpx.Response(503)
        if request.method == "DELETE":
            return httpx.Response(409)
        return httpx.Response(
            200,
            json={
                "uuid": OPERATION,
                "kind": "instance",
                "status": "SUCCESS",
                "image_uuid": IMAGE,
                "result_image_uuid": None,
            },
        )

    report = run_smoke(
        config(),
        allow_live=True,
        transport=httpx.MockTransport(respond),
        sleep=lambda seconds: None,
    )
    assert report.compute_terminal_confirmed
    assert report.status == "NOT_VERIFIED"
    assert report.error_class == "S_UNAVAILABLE"
    assert methods == ["POST", "GET", "DELETE", "GET"]


def test_backend_cannot_read_or_cancel_another_operation_or_submit_twice() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"uuid": OPERATION})

    backend = NebiusSandboxBackend(config(), transport=httpx.MockTransport(respond))
    try:
        own_id = backend.start_probe()
        with pytest.raises(RunnerError, match="S_DUPLICATE_SUBMISSION"):
            backend.start_probe()
        for operation_id in (UUID(IMAGE),):
            with pytest.raises(RunnerError, match="S_POLICY_DENIED"):
                backend.terminate(operation_id)
            with pytest.raises(RunnerError, match="S_POLICY_DENIED"):
                backend.inspect(operation_id)
        assert own_id == UUID(OPERATION)
        assert len(calls) == 1
    finally:
        backend.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "S_AUTH"),
        (403, "S_AUTH"),
        (302, "S_REDIRECT_DENIED"),
        (500, "S_SUBMISSION_UNKNOWN"),
    ],
)
def test_submission_failure_never_follows_redirect_or_retries(
    status: int, code: str
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status, text="test-key", headers={"location": "https://evil.example"}
        )

    report = run_smoke(
        config(), allow_live=True, transport=httpx.MockTransport(respond)
    )
    assert report.error_class == code
    assert len(calls) == 1
    assert "test-key" not in report.model_dump_json()


def test_schema_failure_after_spawn_retains_owned_id_and_attempts_cancellation() -> (
    None
):
    methods = []

    def respond(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": OPERATION})
        if request.method == "DELETE":
            return httpx.Response(202)
        return httpx.Response(200, content=b"bad-json")

    report = run_smoke(
        config(),
        allow_live=True,
        transport=httpx.MockTransport(respond),
        sleep=lambda seconds: None,
    )
    assert report.error_class == "S_SCHEMA_INVALID"
    assert not report.compute_terminal_confirmed
    assert report.operation_id == UUID(OPERATION)
    assert methods == ["POST", "GET", "DELETE", "GET"]


def test_cli_default_and_missing_configuration_never_construct_backend(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import check_sandbox

    monkeypatch.setattr("sys.argv", ["check_sandbox"])
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "project-test")
    monkeypatch.setenv("SANDBOX_IMAGE_UUID", IMAGE)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("offline CLI constructed backend")

    monkeypatch.setattr("services.runner.smoke.NebiusSandboxBackend", forbidden)
    assert check_sandbox.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_RUN"
    monkeypatch.delenv("SANDBOX_IMAGE_UUID")
    assert check_sandbox.main() == 2
    assert json.loads(capsys.readouterr().out)["error_class"] == "S_CONFIG_MISSING"


@pytest.mark.parametrize(
    "content", [b"invalid-json", b"x" * 65537], ids=["invalid-json", "too-large"]
)
def test_unreadable_spawn_response_is_unknown_without_resubmission(
    content: bytes,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, content=content)

    report = run_smoke(
        config(), allow_live=True, transport=httpx.MockTransport(respond)
    )
    assert report.error_class == "S_SUBMISSION_UNKNOWN"
    assert report.operation_id is None
    assert not report.compute_terminal_confirmed
    assert len(calls) == 1


def test_expired_deadline_does_not_issue_another_read() -> None:
    from services.runner.responses import Operation
    from services.runner.smoke import _wait

    class FakeBackend:
        """Test-only adapter that must never execute or contact any service."""

        def start_probe(self) -> UUID:
            pytest.fail("unexpected spawn")

        def inspect(self, operation_id: UUID) -> Operation:
            pytest.fail("deadline did not stop inspection")

        def terminate(self, operation_id: UUID) -> None:
            pytest.fail("unexpected cancellation")

    with pytest.raises(RunnerError, match="S_TIMEOUT"):
        _wait(
            FakeBackend(),
            UUID(OPERATION),
            polls=1,
            deadline=-1,
            sleep=lambda seconds: None,
        )
