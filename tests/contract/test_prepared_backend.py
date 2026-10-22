"""Mock HTTP only; small inert command plans, no guest or credential use."""

import json
from uuid import uuid4

import httpx
import pytest

from packages.contracts.sandbox import RunnerError
from services.runner.admission import AdmissionDenied
from services.runner.prepared import PreparedImageBinding, PreparedNebiusBackend
from services.runner.receipts import plan_ref
from tests.contract.test_sandbox_smoke import config
from tests.unit.test_runner_admission import fixture


def inputs():
    plan, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
    binding = PreparedImageBinding(
        plan_ref=plan_ref(plan),
        execution_id=uuid4(),
        tree_ref="sha256:" + "8" * 64,
        image_uuid=config().image_uuid,
        image_digest=plan.request.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
    )
    return plan, binding, sign(), verifier


def test_exact_submit_poll_cancel_and_http_lifecycle():
    plan, binding, signed, verifier = inputs()
    operation = uuid4()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["networking"] == {"enabled": False}
            assert body["shell"] is False and body["preserve_env"] is False
            assert body["disposable"] is True
            assert (body["command"], *body["args"]) == plan.argv
            assert "synthetic-test-key" not in request.content.decode()
            return httpx.Response(201, json={"uuid": str(operation)})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "uuid": str(operation),
                    "kind": "instance",
                    "image_uuid": str(binding.image_uuid),
                    "result_image_uuid": None,
                    "status": "EXECUTING",
                },
            )
        return httpx.Response(202)

    with PreparedNebiusBackend(
        config(), binding, signed, verifier, transport=httpx.MockTransport(handler)
    ) as backend:
        assert backend.origin == "TEST_TRANSPORT"
        assert backend.submit(plan, binding.execution_id, binding.tree_ref) == operation
        assert backend.inspect(operation).status == "EXECUTING"
        backend.terminate(operation)
        with pytest.raises(RunnerError):
            backend.submit(plan, binding.execution_id, binding.tree_ref)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "fault", ["plan", "execution", "tree", "image", "signature", "live"]
)
def test_denial_makes_no_http_request(fault):
    plan, binding, signed, verifier = inputs()

    def forbidden(request):
        pytest.fail("denied request reached transport")

    if fault == "image":
        binding = binding.model_copy(update={"image_uuid": uuid4()})
    if fault == "signature":
        signed = signed.model_copy(update={"signature": "0" * 128})
    with pytest.raises((RunnerError, AdmissionDenied)):
        with PreparedNebiusBackend(
            config(),
            binding,
            signed,
            verifier,
            transport=None if fault == "live" else httpx.MockTransport(forbidden),
        ) as backend:
            backend.submit(
                plan.model_copy(update={"trace_id": uuid4()})
                if fault == "plan"
                else plan,
                uuid4() if fault == "execution" else binding.execution_id,
                "sha256:" + "9" * 64 if fault == "tree" else binding.tree_ref,
            )


@pytest.mark.parametrize("fault", ["timeout", "schema", "redirect", "auth"])
def test_unknown_or_denied_submission_is_never_retried(fault):
    plan, binding, signed, verifier = inputs()
    calls = []

    def handler(request):
        calls.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(
            {"schema": 201, "redirect": 302, "auth": 403}[fault], json={}
        )

    with PreparedNebiusBackend(
        config(), binding, signed, verifier, transport=httpx.MockTransport(handler)
    ) as backend:
        with pytest.raises((RunnerError, TimeoutError)):
            backend.submit(plan, binding.execution_id, binding.tree_ref)
        with pytest.raises(RunnerError):
            backend.submit(plan, binding.execution_id, binding.tree_ref)
    assert len(calls) == 1


def test_unowned_operation_is_never_read_or_cancelled():
    plan, binding, signed, verifier = inputs()
    with PreparedNebiusBackend(
        config(),
        binding,
        signed,
        verifier,
        transport=httpx.MockTransport(lambda r: pytest.fail("unowned request")),
    ) as backend:
        with pytest.raises(RunnerError):
            backend.inspect(uuid4())
        with pytest.raises(RunnerError):
            backend.terminate(uuid4())


@pytest.mark.parametrize("fault", ["image", "operation", "schema"])
def test_polling_rejects_mismatched_or_invalid_provider_observations(fault):
    plan, binding, signed, verifier = inputs()
    operation = uuid4()

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": str(operation)})
        if fault == "schema":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "uuid": str(uuid4() if fault == "operation" else operation),
                "kind": "instance",
                "status": "SUCCESS",
                "result_image_uuid": None,
                "image_uuid": str(uuid4() if fault == "image" else binding.image_uuid),
            },
        )

    with PreparedNebiusBackend(
        config(), binding, signed, verifier, transport=httpx.MockTransport(handler)
    ) as backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        with pytest.raises(RunnerError):
            backend.inspect(operation)


@pytest.mark.parametrize("status", ["SUCCESS", "FAILED"])
def test_saved_junit_collects_exact_private_bytes(status):
    from hashlib import sha256

    plan, binding, signed, verifier = inputs()
    plan = plan.model_copy(update={"command_id": "PYTEST_FILES_SAVED_JUNIT_V1"})
    binding = binding.model_copy(update={"plan_ref": plan_ref(plan)})
    operation, result_image = uuid4(), uuid4()
    report = b'<testsuite tests="1"><testcase name="demo"/></testsuite>'
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["disposable"] is False
            return httpx.Response(201, json={"uuid": str(operation)})
        if "/operations/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "uuid": str(operation),
                    "kind": "instance",
                    "status": status,
                    "image_uuid": str(binding.image_uuid),
                    "result_image_uuid": str(result_image),
                },
            )
        assert request.url.path == f"/sandboxes/v1/inspect/{result_image}/download"
        assert request.url.params["path"] == "/tmp/replayforge-result.xml"
        return httpx.Response(200, content=report)

    with PreparedNebiusBackend(
        config(), binding, signed, verifier, transport=httpx.MockTransport(handler)
    ) as backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        result = backend.collect_junit(operation)
        assert result.content == report
        assert result.report_ref == "sha256:" + sha256(report).hexdigest()
        assert result.origin == "TEST_TRANSPORT"
        assert not result.can_authorize_verification
        assert "<testsuite" not in repr(result)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "disabled",
        "unowned",
        "running",
        "cancelled",
        "missing_image",
        "source_image",
        "empty",
        "oversize",
        "redirect",
        "missing_file",
    ],
)
def test_junit_export_denies_invalid_or_unavailable_reports(fault):
    plan, binding, signed, verifier = inputs()
    if fault != "disabled":
        plan = plan.model_copy(update={"command_id": "PYTEST_FILES_SAVED_JUNIT_V1"})
        binding = binding.model_copy(update={"plan_ref": plan_ref(plan)})
    operation = uuid4()
    downloads = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": str(operation)})
        if "/operations/" in request.url.path:
            image = (
                None
                if fault == "missing_image"
                else str(binding.image_uuid)
                if fault == "source_image"
                else str(uuid4())
            )
            return httpx.Response(
                200,
                json={
                    "uuid": str(operation),
                    "kind": "instance",
                    "status": "EXECUTING"
                    if fault == "running"
                    else "CANCELLED"
                    if fault == "cancelled"
                    else "SUCCESS",
                    "image_uuid": str(binding.image_uuid),
                    "result_image_uuid": image,
                },
            )
        downloads.append(request)
        return httpx.Response(
            302 if fault == "redirect" else 404 if fault == "missing_file" else 200,
            content=b"x" * 65537 if fault == "oversize" else b"",
        )

    with PreparedNebiusBackend(
        config(), binding, signed, verifier, transport=httpx.MockTransport(handler)
    ) as backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        with pytest.raises(RunnerError):
            backend.collect_junit(uuid4() if fault == "unowned" else operation)
    assert len(downloads) == (
        1 if fault in {"empty", "oversize", "redirect", "missing_file"} else 0
    )
