import pytest
from pydantic import ValidationError

from services.verifier.gates import REQUIRED_GATES, GateEvidence, evaluate


def evidence(
    gate: str, status: str = "PASS", workspace: str = "a" * 64
) -> GateEvidence:
    return GateEvidence(
        gate=gate,
        status=status,
        evidence_origin="REMOTE_SERVICE",
        execution_ref="sha256:" + "b" * 64,
        source_commit="d" * 40,
        patch_ref="sha256:" + "e" * 64,
        workspace_sha256=workspace,
    )


def test_missing_or_failed_gate_never_verifies() -> None:
    assert evaluate((evidence("BASELINE_REPRODUCED"),)).status == "INCONCLUSIVE"
    records = tuple(evidence(gate) for gate in REQUIRED_GATES)
    records = records[:-1] + (evidence("ARTIFACT_INTEGRITY_PASS", "FAIL"),)
    assert evaluate(records).status == "NOT_VERIFIED"


def test_only_complete_same_workspace_evidence_verifies() -> None:
    records = tuple(evidence(gate) for gate in REQUIRED_GATES)
    assert evaluate(records).status == "VERIFIED"
    assert (
        evaluate(
            records[:-1] + (evidence("ARTIFACT_INTEGRITY_PASS", workspace="c" * 64),)
        ).status
        == "INCONCLUSIVE"
    )


def test_mixed_source_or_patch_evidence_cannot_verify() -> None:
    records = tuple(evidence(gate) for gate in REQUIRED_GATES)
    assert (
        evaluate(
            records[:-1]
            + (
                evidence("ARTIFACT_INTEGRITY_PASS").model_copy(
                    update={"patch_ref": "sha256:" + "f" * 64}
                ),
            )
        ).status
        == "INCONCLUSIVE"
    )
    assert (
        evaluate(
            records[:-1]
            + (
                evidence("ARTIFACT_INTEGRITY_PASS").model_copy(
                    update={"source_commit": "f" * 40}
                ),
            )
        ).status
        == "INCONCLUSIVE"
    )


def test_model_claim_is_not_valid_gate_evidence() -> None:
    with pytest.raises(ValidationError):
        GateEvidence(
            gate="BASELINE_REPRODUCED",
            status="PASS",
            evidence_origin="MODEL",
            execution_ref="sha256:" + "b" * 64,
            source_commit="d" * 40,
            patch_ref="sha256:" + "e" * 64,
            workspace_sha256="a" * 64,
        )
