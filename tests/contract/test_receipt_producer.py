"""Inert tiny archives, synthetic provider HTTP and ephemeral test signing keys."""

import tarfile
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.contracts.sandbox import RunnerError
from services.runner.baseline import FailureExpectation
from services.runner.producer import MeasuredPreparedBackend
from services.runner.receipts import (
    ReceiptDenied,
    ReceiptVerifier,
    SignedReceipt,
    plan_ref,
)
from services.runner.workspace import archive_identity
from tests.contract.test_prepared_backend import inputs
from tests.contract.test_sandbox_smoke import config
from tests.contract.test_workspace_measurement import archive
from tests.unit.test_baseline_inspection import XML


def setup(fault=None, *, phase="BASELINE", exit_code=1, body=XML):
    plan, binding, attestation, verifier = inputs()
    request = plan.request.model_copy(
        update={
            "phase": phase,
            "patch_ref": None if phase == "BASELINE" else "sha256:" + "7" * 64,
        }
    )
    plan = plan.model_copy(
        update={
            "command_id": "PYTEST_FILES_MEASURED_V1",
            "request": request,
            "argv": (plan.argv[0], "-B", *plan.argv[1:]),
        }
    )
    before = archive([("app.py", b"x=1\n", tarfile.REGTYPE)])
    after = (
        archive([("app.py", b"x=2\n", tarfile.REGTYPE)])
        if fault in {"mutated", "wrong_source"}
        else before
    )
    tree = archive_identity(before)[0]
    binding = binding.model_copy(update={"plan_ref": plan_ref(plan), "tree_ref": tree})
    operation, result_image = uuid4(), uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            if fault == "timeout":
                raise httpx.ReadTimeout("private transport data", request=request)
            return httpx.Response(201, json={"uuid": str(operation)})
        if "/operations/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "uuid": str(uuid4() if fault == "operation" else operation),
                    "kind": "instance",
                    "status": "EXECUTING" if fault == "pending" else "SUCCESS",
                    "image_uuid": str(binding.image_uuid),
                    "result_image_uuid": str(
                        binding.image_uuid if fault == "same_image" else result_image
                    ),
                    "metadata": {
                        "result": {
                            "state": {
                                "exit_code": True if fault == "state" else exit_code,
                                "timed_out": fault == "timed_out",
                                "signal": -1 if fault == "no_signal_sentinel" else None,
                            },
                            "stdout": "private-stdout-never-retain",
                        }
                    },
                },
            )
        if request.url.path.endswith("/archive"):
            return httpx.Response(
                200,
                content=before
                if fault != "wrong_source"
                and str(binding.image_uuid) in request.url.path
                else after,
            )
        return httpx.Response(200, content=body)

    key = Ed25519PrivateKey.generate()
    backend = MeasuredPreparedBackend(
        config(),
        binding,
        attestation,
        verifier,
        receipt_key=key,
        receipt_key_id="test-receipts",
        transport=httpx.MockTransport(handler),
    )
    return backend, plan, binding, operation, calls, key


def test_producer_signs_actual_baseline_and_cached_reads_preserve_time():
    backend, plan, binding, operation, calls, key = setup()
    with backend:
        assert backend.submit(plan, binding.execution_id, binding.tree_ref) == operation
        result = backend.collect_receipt(operation)
        count = len(calls)
        assert backend.collect_receipt(operation) == result
        assert len(calls) == count == 5
        assert isinstance(result.signed, SignedReceipt)
        assert result.signed.payload.source_tree_ref == binding.tree_ref
        assert result.signed.payload.final_tree_ref == binding.tree_ref
        assert result.signed.payload.origin == "TEST_TRANSPORT"
        checked = ReceiptVerifier(
            {"test-receipts": key.public_key()}, origin="TEST_TRANSPORT"
        ).inspect_baseline(
            result.signed,
            plan,
            result.junit,
            execution_id=binding.execution_id,
            source_tree_ref=binding.tree_ref,
            expected=FailureExpectation(
                test_name="test_duplicate", assertion_marker="RF_DUPLICATE_TRANSACTION"
            ),
            now=datetime.now(UTC),
        )
        assert checked.status == "MATCHED_SIGNED_REPORT"
        assert "private-stdout" not in result.model_dump_json()
        assert "<testsuites" not in repr(result)
        assert not result.can_authorize_verification


def test_provider_minus_one_is_no_signal_not_a_termination_claim():
    backend, plan, binding, operation, _, _ = setup("no_signal_sentinel")
    with backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        assert backend.collect_receipt(operation).signed.payload.signal is None


@pytest.mark.parametrize("fault", ["pending", "operation", "same_image", "state"])
def test_no_receipt_on_unqualified_provider_result(fault):
    backend, plan, binding, operation, _, _ = setup(fault)
    with backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        with pytest.raises(RunnerError):
            backend.collect_receipt(operation)


@pytest.mark.parametrize("fault", ["mutated", "timed_out"])
def test_signed_failing_facts_cannot_satisfy_verifier(fault):
    backend, plan, binding, operation, _, key = setup(fault)
    with backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        result = backend.collect_receipt(operation)
        with pytest.raises(ReceiptDenied):
            ReceiptVerifier(
                {"test-receipts": key.public_key()}, origin="TEST_TRANSPORT"
            ).inspect_baseline(
                result.signed,
                plan,
                result.junit,
                execution_id=binding.execution_id,
                source_tree_ref=binding.tree_ref,
                expected=FailureExpectation(
                    test_name="test_duplicate",
                    assertion_marker="RF_DUPLICATE_TRANSACTION",
                ),
                now=datetime.now(UTC),
            )


def test_uncertain_submit_stays_sticky_and_never_produces_receipt():
    backend, plan, binding, operation, calls, _ = setup("timeout")
    with backend:
        with pytest.raises(TimeoutError):
            backend.submit(plan, binding.execution_id, binding.tree_ref)
        with pytest.raises(RunnerError):
            backend.submit(plan, binding.execution_id, binding.tree_ref)
        with pytest.raises(RunnerError):
            backend.collect_receipt(operation)
    assert [r.method for r in calls] == ["GET", "POST"]


def test_mismatched_tree_and_unowned_collection_are_denied_without_submission():
    backend, plan, binding, _, calls, _ = setup()
    with backend:
        with pytest.raises(RunnerError):
            backend.submit(plan, binding.execution_id, "sha256:" + "0" * 64)
        with pytest.raises(RunnerError):
            backend.collect_receipt(uuid4())
    assert not calls


@pytest.mark.parametrize("phase", ["CANDIDATE", "CLEAN_REPLAY"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_replay_purpose_and_actual_exit_are_verified(phase, exit_code):
    from services.runner.receipts import SignedReplayReceipt
    from tests.unit.test_replay_receipts import XML as passing

    backend, plan, binding, operation, _, key = setup(
        phase=phase, exit_code=exit_code, body=passing
    )
    with backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        result = backend.collect_receipt(operation)
        assert isinstance(result.signed, SignedReplayReceipt)
        checked = ReceiptVerifier(
            {"test-receipts": key.public_key()}, origin="TEST_TRANSPORT"
        ).inspect_replay(
            result.signed,
            plan,
            result.junit,
            execution_id=binding.execution_id,
            source_tree_ref=binding.tree_ref,
            test_name="test_duplicate",
            now=datetime.now(UTC),
        )
        assert checked.reason == ("PASS" if exit_code == 0 else "PROCESS_FAILURE")


def test_actual_prepared_image_mismatch_blocks_post():
    backend, plan, binding, _, calls, _ = setup("wrong_source")
    with backend:
        with pytest.raises(RunnerError, match="S_MEASURED_TREE_MISMATCH"):
            backend.submit(plan, binding.execution_id, binding.tree_ref)
    assert [r.method for r in calls] == ["GET"]


@pytest.mark.parametrize("fault", ["key", "key_id", "admission", "command", "closed"])
def test_invalid_configuration_or_admission_never_measures_or_submits(fault):
    backend, plan, binding, _, calls, _ = setup()
    if fault in {"key", "key_id"}:
        _, _, attestation, verifier = inputs()
        with pytest.raises(RunnerError):
            MeasuredPreparedBackend(
                config(),
                binding,
                attestation,
                verifier,
                receipt_key=None if fault == "key" else Ed25519PrivateKey.generate(),
                receipt_key_id="bad key" if fault == "key_id" else "test",
                transport=httpx.MockTransport(lambda r: pytest.fail("network")),
            )
    elif fault == "admission":
        backend._attestation = backend._attestation.model_copy(
            update={"signature": "0" * 128}
        )
    elif fault == "closed":
        backend.close()
    else:
        plan = plan.model_copy(
            update={
                "command_id": "PYTEST_FILES_SAVED_JUNIT_V1",
                "argv": (plan.argv[0], *plan.argv[2:]),
            }
        )
        backend._binding = binding.model_copy(update={"plan_ref": plan_ref(plan)})
    if fault not in {"key", "key_id"}:
        from services.runner.admission import AdmissionDenied

        with pytest.raises((RunnerError, AdmissionDenied)):
            backend.submit(plan, binding.execution_id, binding.tree_ref)
    backend.close()
    assert not calls
