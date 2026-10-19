"""Ephemeral synthetic authority tests; no live runner verification is claimed."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.runner.receipts import canonical
from services.verifier.gates import REQUIRED_GATES, GateEvidence
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
    )
    records = tuple(
        GateEvidence(
            gate=gate,
            status="PASS",
            evidence_origin="REMOTE_SERVICE",
            execution_ref="sha256:" + hashlib.sha256(artifacts[i].content).hexdigest(),
            execution_id=uuid4(),
            source_commit=context.source_commit,
            patch_ref=context.patch_ref,
            workspace_sha256=context.workspace_sha256,
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
    assert not decision.can_authorize_publication
    assert decision.manifest_ref == manifest.manifest_id


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
            payload = payload.model_copy(
                update={
                    "gates": tuple(
                        g.model_copy(
                            update={"execution_id": payload.gates[0].execution_id}
                        )
                        for g in payload.gates
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
    key, signed, expected, manifest, artifacts, verifier = prepared()
    records = tuple(r for r in signed.payload.gates if r.gate != gate)
    record = signed.payload.gates[-1].model_copy(
        update={"gate": gate, "status": "FAIL"}
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
