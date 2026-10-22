"""Small mocked HTTP observations; no guest execution or production signing."""

from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from packages.contracts.sandbox import RunnerError
from services.runner.observations import ReplayObservation, read_replay_observation
from tests.contract.test_prepared_backend import inputs
from tests.contract.test_sandbox_smoke import config


def data():
    plan = inputs()[0].model_copy(update={"command_id": "PYTEST_FILES_SAVED_JUNIT_V1"})
    operation, image, execution = uuid4(), uuid4(), uuid4()
    payload = {
        "uuid": str(operation),
        "kind": "instance",
        "status": "SUCCESS",
        "image_uuid": str(config().image_uuid),
        "result_image_uuid": str(image),
        "metadata": {
            "result": {
                "state": {"exit_code": 1, "signal": None, "timed_out": False},
                "stdout": "synthetic-secret-never-retain",
            }
        },
    }
    return plan, operation, image, execution, payload


@pytest.mark.parametrize(
    "fault",
    ["operation", "image", "state", "result", "large", "empty", "redirect", "timeout"],
)
def test_observation_reader_rejects_bad_correlation_schema_and_transport(fault):
    plan, operation, _, execution, payload = data()
    if fault == "operation":
        payload["uuid"] = str(uuid4())
    if fault == "image":
        payload["image_uuid"] = str(uuid4())
    if fault == "state":
        payload["metadata"]["result"]["state"]["exit_code"] = True
    if fault == "result":
        payload["metadata"]["result"] = None

    def handler(request):
        if fault == "timeout":
            raise httpx.ReadTimeout("private provider details", request=request)
        if "/operations/" in request.url.path:
            return httpx.Response(200, json=payload)
        return httpx.Response(
            302 if fault == "redirect" else 200,
            content=b"x" * 65537 if fault == "large" else b"",
        )

    with pytest.raises(RunnerError):
        read_replay_observation(
            config(),
            plan,
            execution,
            operation,
            "sha256:" + "4" * 64,
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.parametrize("status", ["EXECUTING", "CANCELLED", "FAILED"])
def test_observation_reader_distinguishes_pending_and_missing_report(status):
    plan, operation, _, execution, payload = data()
    payload.update(status=status, result_image_uuid=None)
    observed = read_replay_observation(
        config(),
        plan,
        execution,
        operation,
        "sha256:" + "4" * 64,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
    )
    assert observed.status == (
        "PENDING" if status == "EXECUTING" else "TERMINAL_NO_REPORT"
    )
    assert observed.junit_bytes() is None
    assert not observed.can_authorize_verification
    assert "synthetic-secret-never-retain" not in observed.model_dump_json()
    assert (
        observed.process_state.exit_code == 1
        if status == "FAILED"
        else observed.process_state is None
    )


def test_observation_live_and_nonexport_denial_happens_before_http():
    plan, operation, _, execution, _ = data()
    with pytest.raises(RunnerError, match="S_LIVE_NOT_AUTHORIZED"):
        read_replay_observation(
            config(), plan, execution, operation, "sha256:" + "4" * 64
        )
    with pytest.raises(RunnerError, match="S_REPORT_EXPORT_DISABLED"):
        read_replay_observation(
            config(),
            plan.model_copy(update={"command_id": "PYTEST_FILES_V1"}),
            execution,
            operation,
            "sha256:" + "4" * 64,
            transport=httpx.MockTransport(
                lambda r: pytest.fail("I/O before export validation")
            ),
        )


def test_observation_bytes_are_hash_bound_and_cannot_claim_verification():
    plan, operation, _, execution, payload = data()

    def handler(request):
        return (
            httpx.Response(200, json=payload)
            if "/operations/" in request.url.path
            else httpx.Response(200, content=b"<testsuite/>")
        )

    observation = read_replay_observation(
        config(),
        plan,
        execution,
        operation,
        "sha256:" + "4" * 64,
        transport=httpx.MockTransport(handler),
    )
    for change in [
        {"junit_ref": "sha256:" + "9" * 64},
        {"junit_b64": "not-base64"},
        {"can_authorize_verification": True},
    ]:
        with pytest.raises(ValidationError):
            ReplayObservation.model_validate({**observation.model_dump(), **change})
