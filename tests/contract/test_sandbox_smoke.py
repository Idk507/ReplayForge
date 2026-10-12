"""Offline tests of the real REST adapter; no guest code runs on this host."""

import base64
import hashlib
import json
from uuid import UUID

import httpx
import pytest

from packages.contracts.sandbox import ProbeStatus
from services.runner.remote import SandboxConfig, SandboxConfigError
from services.runner.smoke import run_smoke

IMAGE = "11111111-1111-4111-8111-111111111111"
OPERATION = "22222222-2222-4222-8222-222222222222"
SECRET = "synthetic-test-key"


def config(**overrides: str) -> SandboxConfig:
    return SandboxConfig.from_env(
        {
            "NEBIUS_API_KEY": SECRET,
            "NEBIUS_PROJECT_ID": "project-test",
            "SANDBOX_IMAGE_UUID": IMAGE,
            **overrides,
        }
    )


def terminal(request_body: dict[str, object]) -> dict[str, object]:
    output = json.dumps(
        {
            "python_version": "Python 3.12.12",
            "uid": 1000,
            "gid": 1000,
            "interfaces": ["lo"],
            "docker_socket_present": False,
            "credential_env_present": False,
        }
    )
    return {
        "uuid": OPERATION,
        "kind": "instance",
        "status": "SUCCESS",
        "image_uuid": IMAGE,
        "result_image_uuid": None,
        "metadata": {
            **request_body,
            "result": {
                "state": {"exit_code": 0, "signal": -1, "timed_out": False},
                "stdout": {
                    "value": base64.b64encode(output.encode()).decode(),
                    "encoding": "base64",
                    "truncated": False,
                },
                "stderr": {"value": "", "encoding": "ascii", "truncated": False},
            },
        },
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"NEBIUS_API_KEY": ""},
        {"NEBIUS_PROJECT_ID": ""},
        {"NEBIUS_PROJECT_ID": "project\r\nInjected: true"},
        {"SANDBOX_IMAGE_UUID": "tag:python:latest"},
        {"SANDBOX_REMOTE_URL": "http://api.tokenfactory.nebius.com/sandboxes/"},
        {"SANDBOX_REMOTE_URL": "https://evil.example/sandboxes/"},
        {
            "SANDBOX_REMOTE_URL": "https://user:secret@api.tokenfactory.nebius.com/sandboxes/"
        },
        {"SANDBOX_REMOTE_URL": "https://api.tokenfactory.nebius.com/sandboxes/?key=x"},
    ],
)
def test_bad_configuration_fails_closed(overrides: dict[str, str]) -> None:
    with pytest.raises(SandboxConfigError):
        config(**overrides)


def test_live_opt_in_required_before_constructing_transport() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("offline smoke made a network request")

    report = run_smoke(
        config(), allow_live=False, transport=httpx.MockTransport(forbidden)
    )
    assert report.status == ProbeStatus.NOT_RUN
    assert report.evidence_origin == "NOT_RUN"
    assert report.operation_id is None


def test_fixed_probe_uses_least_privilege_and_records_safe_evidence() -> None:
    body: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.headers["project"] == "project-test"
        if request.method == "POST":
            body.update(json.loads(request.content))
            assert request.url.path == "/sandboxes/v1/instances"
            assert body["shell"] is False
            assert body["env"] == {}
            assert body["preserve_env"] is False
            assert body["networking"] == {"enabled": False}
            assert body["disposable"] is True
            assert body["uid"] == body["gid"] == 1000
            assert body["image"] == IMAGE
            assert SECRET not in request.content.decode()
            return httpx.Response(201, json={"uuid": OPERATION})
        return httpx.Response(200, json=terminal(body))

    report = run_smoke(
        config(),
        allow_live=True,
        transport=httpx.MockTransport(respond),
        sleep=lambda seconds: None,
    )
    assert report.status == ProbeStatus.PASSED
    assert report.evidence_origin == "TEST_TRANSPORT"
    assert report.operation_id == UUID(OPERATION)
    assert report.compute_terminal_confirmed
    assert report.image_uuid == UUID(IMAGE)
    assert report.artifacts[0].sha256
    assert report.artifacts[0].artifact_id.startswith("sha256:")
    assert report.observation is not None
    canonical = json.dumps(
        report.observation.model_dump(), sort_keys=True, separators=(",", ":")
    ).encode()
    assert report.artifacts[0].sha256 == hashlib.sha256(canonical).hexdigest()
    assert report.artifacts[0].size_bytes == len(canonical)
    assert report.python_version == "Python 3.12.12"
    assert SECRET not in report.model_dump_json()
    assert report.policy_decision == "ALLOW_M0_SYNTHETIC_PROBE"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("nonzero", "S_PROCESS_FAILED"),
        ("timeout", "S_PROCESS_FAILED"),
        ("signal", "S_PROCESS_FAILED"),
        ("truncated", "S_OUTPUT_INVALID"),
        ("network", "S_ISOLATION_FAILED"),
        ("root", "S_ISOLATION_FAILED"),
        ("secret", "S_ISOLATION_FAILED"),
        ("image", "S_CORRELATION_FAILED"),
        ("operation", "S_CORRELATION_FAILED"),
        ("saved", "S_POLICY_DENIED"),
        ("network-policy", "S_POLICY_DENIED"),
        ("bad-base64", "S_OUTPUT_INVALID"),
        ("numeric-network", "S_POLICY_DENIED"),
        ("missing-disposable-evidence", "S_SCHEMA_INVALID"),
        ("injected-files", "S_POLICY_DENIED"),
        ("docker-socket", "S_ISOLATION_FAILED"),
    ],
)
def test_operation_success_is_not_process_or_isolation_success(
    mutation: str, code: str
) -> None:
    body: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body.update(json.loads(request.content))
            return httpx.Response(201, json={"uuid": OPERATION})
        data = terminal(body)
        metadata = data["metadata"]
        assert isinstance(metadata, dict)
        result = metadata["result"]
        if mutation in {"nonzero", "timeout", "signal"}:
            field = {
                "nonzero": "exit_code",
                "timeout": "timed_out",
                "signal": "signal",
            }[mutation]
            result["state"][field] = True if mutation == "timeout" else 9
        elif mutation == "truncated":
            result["stdout"]["truncated"] = True
        elif mutation in {"network", "root", "secret", "docker-socket"}:
            output = {
                "python_version": "Python 3.12.12",
                "uid": 1000,
                "gid": 1000,
                "interfaces": ["lo"],
                "docker_socket_present": False,
                "credential_env_present": False,
            }
            output[
                {
                    "network": "interfaces",
                    "root": "uid",
                    "secret": "credential_env_present",
                    "docker-socket": "docker_socket_present",
                }[mutation]
            ] = {
                "network": ["lo", "eth0"],
                "root": 0,
                "secret": True,
                "docker-socket": True,
            }[mutation]
            result["stdout"] = {
                "value": json.dumps(output),
                "encoding": "ascii",
                "truncated": False,
            }
        elif mutation in {"image", "operation"}:
            data["image_uuid" if mutation == "image" else "uuid"] = (
                "33333333-3333-4333-8333-333333333333"
            )
        elif mutation == "saved":
            data["result_image_uuid"] = IMAGE
        elif mutation == "network-policy":
            metadata["networking"] = {"enabled": True}
        elif mutation == "numeric-network":
            metadata["networking"] = {"enabled": 0}
        elif mutation == "missing-disposable-evidence":
            del data["result_image_uuid"]
        elif mutation == "injected-files":
            metadata["files"] = {"/tmp/unexpected": {"uuid": IMAGE}}
        else:
            result["stdout"]["value"] = "not-base64!"
        return httpx.Response(200, json=data)

    report = run_smoke(
        config(),
        allow_live=True,
        transport=httpx.MockTransport(respond),
        sleep=lambda seconds: None,
    )
    assert report.status == ProbeStatus.NOT_VERIFIED
    assert report.error_class == code
    assert report.operation_id == UUID(OPERATION)


def test_uncertain_submission_is_never_retried() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)

    report = run_smoke(
        config(), allow_live=True, transport=httpx.MockTransport(respond)
    )
    assert report.error_class == "S_SUBMISSION_UNKNOWN"
    assert not report.compute_terminal_confirmed
    assert not report.retryable
    assert len(calls) == 1
    assert SECRET not in report.model_dump_json()


def test_poll_failure_cancels_owned_operation_and_confirms_terminal_state() -> None:
    methods = []

    def respond(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": OPERATION})
        if methods == ["POST", "GET"]:
            raise httpx.ReadTimeout(SECRET, request=request)
        if request.method == "DELETE":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "uuid": OPERATION,
                "kind": "instance",
                "status": "CANCELLED",
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
    assert report.status == ProbeStatus.NOT_VERIFIED
    assert report.error_class == "S_TIMEOUT"
    assert report.compute_terminal_confirmed
    assert methods == ["POST", "GET", "DELETE", "GET"]


def test_unconfirmed_cancellation_never_claims_cleanup() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": OPERATION})
        return httpx.Response(503, text=SECRET)

    report = run_smoke(
        config(),
        allow_live=True,
        transport=httpx.MockTransport(respond),
        sleep=lambda seconds: None,
    )
    assert report.status == ProbeStatus.NOT_VERIFIED
    assert not report.compute_terminal_confirmed
    assert report.cleanup_error_class == "S_UNAVAILABLE"
    assert SECRET not in report.model_dump_json()
