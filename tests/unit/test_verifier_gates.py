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
    records = tuple(
        evidence(gate, "FAIL" if gate == "ARTIFACT_INTEGRITY_PASS" else "PASS")
        for gate in REQUIRED_GATES
    )
    assert evaluate(records).status == "NOT_VERIFIED"


def test_complete_unsigned_evidence_is_only_a_consistency_preview() -> None:
    records = tuple(evidence(gate) for gate in REQUIRED_GATES)
    assert evaluate(records).status == "GATES_SATISFIED"
    assert not evaluate(records).can_authorize_verification
    assert (
        evaluate(
            records[:-1] + (evidence(records[-1].gate, workspace="c" * 64),)
        ).status
        == "INCONCLUSIVE"
    )


def test_mixed_source_or_patch_evidence_cannot_verify() -> None:
    records = tuple(evidence(gate) for gate in REQUIRED_GATES)
    assert (
        evaluate(
            records[:-1]
            + (
                evidence(records[-1].gate).model_copy(
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
                evidence(records[-1].gate).model_copy(
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


@pytest.mark.parametrize("gate", ["EXISTING_SUITE_PASS", "SECURITY_GATE_PASS"])
def test_architecture_mandatory_gates_cannot_be_omitted(gate):
    assert gate in REQUIRED_GATES
    result = evaluate(tuple(evidence(g) for g in REQUIRED_GATES if g != gate))
    assert result.status == "INCONCLUSIVE"
    assert gate in result.missing


def test_missing_status_is_reported_by_name_and_invalid_copy_is_rejected():
    records = tuple(evidence(g) for g in REQUIRED_GATES)
    result = evaluate(records[:-1] + (evidence(records[-1].gate, "MISSING"),))
    assert records[-1].gate in result.missing
    invalid = records[0].model_copy(update={"status": "invented"})
    assert evaluate((invalid, *records[1:])).status == "INCONCLUSIVE"
