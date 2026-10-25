"""Synthetic HTTP and ephemeral test keys; never execute guest code locally."""

import json
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.contracts.sandbox import RunnerError
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.confined import ConfinedMeasuredBackend
from services.runner.confinement import confinement_configuration_ref
from services.runner.receipts import canonical
from tests.contract.test_receipt_producer import setup


def confined(fault=None):
    original, plan, binding, operation, calls, key = setup()
    transport = original._http._client._transport
    configuration = confinement_configuration_ref(
        plan, binding.image_uuid, binding.execution_id
    )
    binding = binding.model_copy(update={"configuration_ref": configuration})
    profile = original._attestation.payload.model_copy(
        update={"configuration_ref": configuration}
    )
    assessor = Ed25519PrivateKey.generate()
    attestation = CapabilityAttestation(
        key_id="fixture", payload=profile, signature="0" * 128
    )
    attestation = attestation.model_copy(
        update={"signature": assessor.sign(canonical(attestation, unsigned=True)).hex()}
    )
    verifier = CapabilityVerifier(
        {"fixture": assessor.public_key()},
        runner_id=binding.runner_id,
        backend="NEBIUS_CONTREE",
        origin="REMOTE_SERVICE",
        configuration_ref=configuration,
    )
    inner = {
        "run_id": str(plan.request.run_id),
        "execution_id": str(binding.execution_id),
        "plan_ref": binding.plan_ref,
        "argv": list(plan.argv),
        "env": dict(plan.env),
        "exit_code": 1,
        "timed_out": False,
        "output_limited": False,
        "confinement": {
            "uid": 1000,
            "gid": 1000,
            "process": {
                "CapEff": "0000000000000000",
                "NoNewPrivs": "1",
                "Seccomp": "2",
            },
            "root_mount_read_only": True,
            "tmp_bytes": 16777216,
            "workspace_bytes": 16777216,
            "cgroup": {
                "cpu.max": "100000 100000",
                "memory.max": "134217728",
                "memory.swap.max": "0",
                "pids.max": "16",
            },
        },
    }
    if fault in {"run_id", "execution_id"}:
        inner[fault] = str(uuid4())
    if fault == "argv":
        inner["argv"] = ["sh"]
    if fault == "boolean_exit":
        inner["exit_code"] = True
    if fault == "outer_exit":
        inner["exit_code"] = 0
    if fault == "root":
        inner["confinement"]["uid"] = 0
    if fault == "seccomp":
        inner["confinement"]["process"]["Seccomp"] = "0"
    if fault == "memory":
        inner["confinement"]["cgroup"]["memory.max"] = "max"
    if fault == "output_limit":
        inner["output_limited"] = True

    def handler(request):
        if request.url.params.get("path") == "/tmp/replayforge-execution.json":
            calls.append(request)
            return httpx.Response(
                200, content=b"{}" if fault == "missing" else json.dumps(inner).encode()
            )
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["uid"] == 0 and payload["env"] == {}
            assert payload["args"][:3] == ["-I", "-S", "-c"]
            assert payload["networking"] == {"enabled": False}
        return transport.handle_request(request)

    from tests.contract.test_sandbox_smoke import config

    backend = ConfinedMeasuredBackend(
        config(),
        binding,
        attestation,
        verifier,
        receipt_key=key,
        receipt_key_id="test-receipts",
        allow_privileged_setup=True,
        transport=httpx.MockTransport(handler),
    )
    return backend, plan, binding, operation, calls


def test_confined_collector_correlates_protected_child_report_before_signing():
    backend, plan, binding, operation, calls = confined()
    with backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        result = backend.collect_receipt(operation)
        assert result.signed.payload.exit_code == 1
        assert result.signed.payload.origin == "TEST_TRANSPORT"
        assert len(calls) == 6


@pytest.mark.parametrize(
    "fault",
    [
        "run_id",
        "execution_id",
        "argv",
        "boolean_exit",
        "outer_exit",
        "root",
        "seccomp",
        "memory",
        "output_limit",
        "missing",
    ],
)
def test_no_receipt_for_forged_or_unconfined_child(fault):
    backend, plan, binding, operation, _ = confined(fault)
    with backend:
        backend.submit(plan, binding.execution_id, binding.tree_ref)
        with pytest.raises(RunnerError):
            backend.collect_receipt(operation)


def test_configuration_drift_is_rejected_before_measurement_or_post():
    backend, plan, binding, _, calls = confined()
    backend._binding = binding.model_copy(
        update={"configuration_ref": "sha256:" + "0" * 64}
    )
    with backend, pytest.raises(RunnerError):
        backend.submit(plan, binding.execution_id, binding.tree_ref)
    assert not calls
