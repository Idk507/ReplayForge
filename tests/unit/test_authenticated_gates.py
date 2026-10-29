"""Ephemeral synthetic authority tests; no live runner verification is claimed."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from services.runner.receipts import canonical
from services.verifier.authenticated import GatePhaseBinding
from services.verifier.gates import (
    EXECUTION_GATES,
    PHASE_BY_GATE,
    REQUIRED_GATES,
    GateEvidence,
)
from services.verifier.manifest import ArtifactInput, build_manifest


def prepared():
    from services.verifier.authenticated import (
        GateContext,
        GatePayload,
        GateVerifier,
        SignedGates,
    )

    artifacts = tuple(
        ArtifactInput(
            name=f"observation-{i}.json",
            content=f'{{"synthetic_observation":{i}}}'.encode(),
        )
        for i in range(len(REQUIRED_GATES))
    ) + (
        ArtifactInput(name="patch.diff", content=b"synthetic patch artifact"),
        ArtifactInput(name="reproduction.py", content=b"def test_synthetic(): pass\n"),
    )
    manifest = build_manifest("a" * 64, artifacts)
    phase_bindings = tuple(
        GatePhaseBinding(
            phase=phase,
            image_uuid=UUID(int=10 + index),
            image_digest="sha256:" + "d" * 64,
            configuration_ref=(
                "sha256:" + "f" * 64
                if index == 0
                else "sha256:" + hashlib.sha256(phase.encode()).hexdigest()
            ),
            workspace_sha256=hashlib.sha256(f"workspace:{phase}".encode()).hexdigest(),
        )
        for index, phase in enumerate(
            ("BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY")
        )
    )
    binding_by_phase = {binding.phase: binding for binding in phase_bindings}
    context = GateContext(
        run_id=uuid4(),
        repository_id=uuid4(),
        run_version=4,
        source_commit="b" * 40,
        patch_ref="sha256:" + hashlib.sha256(artifacts[-2].content).hexdigest(),
        image_digest="sha256:" + "d" * 64,
        configuration_ref="sha256:" + "f" * 64,
        test_ref="sha256:" + hashlib.sha256(artifacts[-1].content).hexdigest(),
        workspace_sha256="a" * 64,
        manifest_ref=manifest.manifest_id,
        phase_bindings=phase_bindings,
    )
    records = tuple(
        GateEvidence(
            gate=gate,
            phase=PHASE_BY_GATE[gate],
            status="PASS",
            evidence_origin="REMOTE_SERVICE",
            execution_ref="sha256:" + hashlib.sha256(artifacts[i].content).hexdigest(),
            execution_id=uuid4(),
            operation_id=uuid4() if gate in EXECUTION_GATES else None,
            image_uuid=binding_by_phase[
                PHASE_BY_GATE[gate]
                if PHASE_BY_GATE[gate] in binding_by_phase
                else "CANDIDATE"
            ].image_uuid,
            image_digest=binding_by_phase[
                PHASE_BY_GATE[gate]
                if PHASE_BY_GATE[gate] in binding_by_phase
                else "CANDIDATE"
            ].image_digest,
            configuration_ref=binding_by_phase[
                PHASE_BY_GATE[gate]
                if PHASE_BY_GATE[gate] in binding_by_phase
                else "CANDIDATE"
            ].configuration_ref,
            source_commit=context.source_commit,
            patch_ref=context.patch_ref,
            workspace_sha256=binding_by_phase[
                PHASE_BY_GATE[gate]
                if PHASE_BY_GATE[gate] in binding_by_phase
                else "CANDIDATE"
            ].workspace_sha256,
        )
        for i, gate in enumerate(REQUIRED_GATES)
    )
    key = Ed25519PrivateKey.generate()
    payload = GatePayload(
        context=context,
        origin="REMOTE_SERVICE",
        observed_at=datetime.now(UTC),
        gates=records,
    )
    signed = SignedGates(
        payload=payload, key_id="synthetic-verifier", signature="0" * 128
    )
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    return (
        key,
        signed,
        context,
        manifest,
        artifacts,
        GateVerifier({"synthetic-verifier": key.public_key()}),
    )


def test_authenticated_complete_gates_verify_with_manifest_bytes():
    _, signed, expected, manifest, artifacts, verifier = prepared()
    decision = verifier.verify(
        signed, expected, manifest, artifacts, now=datetime.now(UTC)
    )
    assert decision.status == "VERIFIED"
    candidate_workspace = next(
        item.workspace_sha256
        for item in expected.phase_bindings
        if item.phase == "CANDIDATE"
    )
    assert decision.context.workspace_sha256 == expected.workspace_sha256
    assert candidate_workspace != expected.workspace_sha256
    assert decision.status == "VERIFIED"
    assert not decision.can_authorize_publication
    assert decision.manifest_ref == manifest.manifest_id


def test_authenticated_gates_allow_distinct_phase_configs_and_workspaces():
    _, signed, expected, manifest, artifacts, verifier = prepared()
    assert len({item.configuration_ref for item in expected.phase_bindings}) > 1
    assert len({item.workspace_sha256 for item in expected.phase_bindings}) > 1
    decision = verifier.verify(
        signed, expected, manifest, artifacts, now=datetime.now(UTC)
    )
    assert decision.status == "VERIFIED"


def test_gate_context_rejects_missing_or_duplicate_phase_bindings():
    _, _, context, _, _, _ = prepared()
    with pytest.raises(ValidationError):
        type(context).model_validate(
            context.model_dump() | {"phase_bindings": context.phase_bindings[1:]}
        )
    with pytest.raises(ValidationError):
        type(context).model_validate(
            context.model_dump()
            | {
                "phase_bindings": (
                    *context.phase_bindings[:-1],
                    context.phase_bindings[0],
                )
            }
        )
    baseline = context.phase_bindings[0].model_copy(
        update={"configuration_ref": "sha256:" + "0" * 64}
    )
    with pytest.raises(ValidationError):
        type(context).model_validate(
            context.model_dump()
            | {"phase_bindings": (baseline, *context.phase_bindings[1:])}
        )


@pytest.mark.parametrize(
    "fault",
    [
        "signature",
        "run",
        "version",
        "source",
        "patch",
        "image",
        "configuration",
        "test",
        "manifest",
        "bytes",
        "omission",
        "expired",
        "future",
        "naive",
        "unknown_key",
        "test_origin",
        "missing_gate",
        "missing_artifact",
        "duplicate_operation",
        "missing_operation",
        "phase_binding",
        "image_binding",
        "image_uuid_binding",
        "configuration_binding",
        "phase_configuration_binding",
        "phase_image_uuid_binding",
        "duplicate_gate",
    ],
)
def test_fabrication_tampering_and_mismatched_context_never_verify(fault):
    from services.verifier.authenticated import GateDenied

    key, signed, expected, manifest, artifacts, verifier = prepared()
    now = datetime.now(UTC)
    if fault == "signature":
        signed = signed.model_copy(update={"signature": "0" * 128})
    elif fault in {
        "run",
        "version",
        "source",
        "patch",
        "image",
        "configuration",
        "test",
        "manifest",
    }:
        name, value = {
            "run": ("run_id", uuid4()),
            "version": ("run_version", 5),
            "source": ("source_commit", "f" * 40),
            "patch": ("patch_ref", "sha256:" + "f" * 64),
            "image": ("image_digest", "sha256:" + "f" * 64),
            "configuration": ("configuration_ref", "sha256:" + "0" * 64),
            "test": ("test_ref", "sha256:" + "f" * 64),
            "manifest": ("manifest_ref", "sha256:" + "f" * 64),
        }[fault]
        expected = expected.model_copy(update={name: value})
    elif fault == "bytes":
        artifacts = (
            ArtifactInput(name=artifacts[0].name, content=b"tampered"),
            *artifacts[1:],
        )
    elif fault == "omission":
        artifacts = artifacts[:-1]
    elif fault == "expired":
        now += timedelta(seconds=301)
    elif fault == "future":
        now -= timedelta(seconds=6)
    elif fault == "naive":
        now = now.replace(tzinfo=None)
    else:
        payload = signed.payload
        if fault == "unknown_key":
            signed = signed.model_copy(update={"key_id": "unknown"})
        elif fault == "test_origin":
            payload = payload.model_copy(update={"origin": "TEST_TRANSPORT"})
        elif fault == "missing_gate":
            payload = payload.model_copy(update={"gates": payload.gates[:-1]})
        elif fault == "duplicate_gate":
            payload = payload.model_copy(
                update={"gates": (*payload.gates[:-1], payload.gates[0])}
            )
        elif fault == "missing_artifact":
            payload = payload.model_copy(
                update={
                    "gates": (
                        payload.gates[0].model_copy(
                            update={"execution_ref": "sha256:" + "f" * 64}
                        ),
                        *payload.gates[1:],
                    )
                }
            )
        elif fault == "duplicate_operation":
            execution = [g for g in payload.gates if g.gate in EXECUTION_GATES]
            first_operation = execution[0].operation_id
            payload = payload.model_copy(
                update={
                    "gates": tuple(
                        g.model_copy(update={"operation_id": first_operation})
                        if g.gate in EXECUTION_GATES
                        else g
                        for g in payload.gates
                    )
                }
            )
        elif fault == "missing_operation":
            first, *rest = payload.gates
            payload = payload.model_copy(
                update={
                    "gates": (first.model_copy(update={"operation_id": None}), *rest)
                }
            )
        elif fault == "phase_binding":
            first = payload.gates[0]
            replacement = (
                "CLEAN_REPLAY" if first.phase != "CLEAN_REPLAY" else "BASELINE"
            )
            payload = payload.model_copy(
                update={
                    "gates": (
                        first.model_copy(update={"phase": replacement}),
                        *payload.gates[1:],
                    )
                }
            )
        elif fault in {
            "image_binding",
            "image_uuid_binding",
            "configuration_binding",
            "phase_configuration_binding",
            "phase_image_uuid_binding",
        }:
            first = payload.gates[0]
            if fault in {"phase_configuration_binding", "phase_image_uuid_binding"}:
                binding = next(
                    b for b in expected.phase_bindings if b.phase == first.phase
                )
                changed = tuple(
                    b.model_copy(
                        update={
                            "configuration_ref": "sha256:" + "0" * 64
                            if fault == "phase_configuration_binding"
                            else b.configuration_ref,
                            "image_uuid": UUID(int=999)
                            if fault == "phase_image_uuid_binding"
                            else b.image_uuid,
                        }
                    )
                    if b.phase == binding.phase
                    else b
                    for b in expected.phase_bindings
                )
                expected = expected.model_copy(update={"phase_bindings": changed})
            else:
                payload = payload.model_copy(
                    update={
                        "gates": (
                            first.model_copy(
                                update={
                                    "image_digest": "sha256:" + "0" * 64
                                    if fault == "image_binding"
                                    else first.image_digest,
                                    "configuration_ref": "sha256:" + "0" * 64
                                    if fault == "configuration_binding"
                                    else first.configuration_ref,
                                    "image_uuid": UUID(int=999)
                                    if fault == "image_uuid_binding"
                                    else first.image_uuid,
                                }
                            ),
                            *payload.gates[1:],
                        )
                    }
                )
        signed = signed.model_copy(update={"payload": payload})
        signed = signed.model_copy(
            update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
        )
    try:
        decision = verifier.verify(signed, expected, manifest, artifacts, now=now)
    except GateDenied:
        return
    assert decision.status != "VERIFIED"


@pytest.mark.parametrize(
    "gate", ["EXISTING_SUITE_PASS", "SECURITY_GATE_PASS", "STATIC_ANALYSIS_PASS"]
)
def test_authenticated_failure_is_not_verified(gate):
    from services.verifier.gates import EXECUTION_GATES, PHASE_BY_GATE

    key, signed, expected, manifest, artifacts, verifier = prepared()
    records = tuple(r for r in signed.payload.gates if r.gate != gate)
    existing = next((r for r in signed.payload.gates if r.gate == gate), None)
    phase = PHASE_BY_GATE[gate]
    binding_phase = (
        phase if phase in {b.phase for b in expected.phase_bindings} else "CANDIDATE"
    )
    phase_binding = next(b for b in expected.phase_bindings if b.phase == binding_phase)
    record = (
        existing.model_copy(update={"status": "FAIL"})
        if existing is not None
        else signed.payload.gates[-1].model_copy(
            update={
                "gate": gate,
                "phase": phase,
                "status": "FAIL",
                "image_uuid": phase_binding.image_uuid,
                "image_digest": phase_binding.image_digest,
                "configuration_ref": phase_binding.configuration_ref,
                "workspace_sha256": phase_binding.workspace_sha256,
                "operation_id": uuid4() if gate in EXECUTION_GATES else None,
                "execution_id": uuid4() if gate in EXECUTION_GATES else None,
            }
        )
    )
    signed = signed.model_copy(
        update={
            "payload": signed.payload.model_copy(update={"gates": (*records, record)})
        }
    )
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    assert (
        verifier.verify(
            signed, expected, manifest, artifacts, now=datetime.now(UTC)
        ).status
        == "NOT_VERIFIED"
    )


def test_matching_new_manifest_cannot_omit_the_signed_test_artifact():
    from services.verifier.authenticated import GateDenied

    key, signed, expected, _, artifacts, verifier = prepared()
    artifacts = artifacts[:-1]
    manifest = build_manifest(expected.workspace_sha256, artifacts)
    expected = expected.model_copy(update={"manifest_ref": manifest.manifest_id})
    signed = signed.model_copy(
        update={"payload": signed.payload.model_copy(update={"context": expected})}
    )
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    with pytest.raises(GateDenied, match="GATE_PATCH_OR_TEST_ARTIFACT_MISSING"):
        verifier.verify(signed, expected, manifest, artifacts, now=datetime.now(UTC))
