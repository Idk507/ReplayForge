"""Minimal deterministic gate evaluator for structured execution evidence."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.gates import GateName as GateName
from packages.contracts.run import ArtifactID, CommitSHA

GatePhase = Literal[
    "BASELINE",
    "CANDIDATE",
    "EXISTING_SUITE",
    "CLEAN_REPLAY",
    "PATCH_SCOPE",
    "ARTIFACT_INTEGRITY",
    "SECURITY",
    "STATIC_ANALYSIS",
]

REQUIRED_GATES: tuple[GateName, ...] = (
    "BASELINE_REPRODUCED",
    "PATCHED_REGRESSION_PASS",
    "EXISTING_SUITE_PASS",
    "CLEAN_REPLAY_PASS",
    "PATCH_SCOPE_PASS",
    "ARTIFACT_INTEGRITY_PASS",
    "SECURITY_GATE_PASS",
)

PHASE_BY_GATE: dict[GateName, GatePhase] = {
    "BASELINE_REPRODUCED": "BASELINE",
    "PATCHED_REGRESSION_PASS": "CANDIDATE",
    "EXISTING_SUITE_PASS": "EXISTING_SUITE",
    "CLEAN_REPLAY_PASS": "CLEAN_REPLAY",
    "PATCH_SCOPE_PASS": "PATCH_SCOPE",
    "ARTIFACT_INTEGRITY_PASS": "ARTIFACT_INTEGRITY",
    "SECURITY_GATE_PASS": "SECURITY",
    "STATIC_ANALYSIS_PASS": "STATIC_ANALYSIS",
}
EXECUTION_PHASES: tuple[GatePhase, ...] = (
    "BASELINE",
    "CANDIDATE",
    "EXISTING_SUITE",
    "CLEAN_REPLAY",
)


def runtime_phase_for_gate(gate: GateName) -> GatePhase:
    """Bind non-execution gates to candidate runtime identity for context."""
    phase = PHASE_BY_GATE[gate]
    return phase if phase in EXECUTION_PHASES else "CANDIDATE"


EXECUTION_GATES: frozenset[GateName] = frozenset(
    {
        "BASELINE_REPRODUCED",
        "PATCHED_REGRESSION_PASS",
        "EXISTING_SUITE_PASS",
        "CLEAN_REPLAY_PASS",
    }
)


class GateEvidence(BaseModel):
    """One prevalidated deterministic observation, never a model assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    gate: GateName
    phase: GatePhase
    status: Literal["PASS", "FAIL", "MISSING"]
    evidence_origin: Literal["REMOTE_SERVICE", "TRUSTED_VERIFIER"]
    execution_ref: ArtifactID
    execution_id: UUID | None = None
    operation_id: UUID | None = None
    image_uuid: UUID
    image_digest: ArtifactID
    configuration_ref: ArtifactID
    source_commit: CommitSHA
    patch_ref: ArtifactID | None = None
    workspace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class VerificationVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["GATES_SATISFIED", "NOT_VERIFIED", "INCONCLUSIVE"]
    missing: tuple[GateName, ...]
    failed: tuple[GateName, ...]
    workspace_sha256: str | None
    source_commit: CommitSHA | None
    patch_ref: ArtifactID | None
    can_authorize_verification: Literal[False] = False


def _inconclusive(
    *, missing: tuple[GateName, ...] = (), failed: tuple[GateName, ...] = ()
) -> VerificationVerdict:
    """Return a closed-safe verdict without binding a candidate to a run."""
    return VerificationVerdict(
        status="INCONCLUSIVE",
        missing=missing,
        failed=failed,
        workspace_sha256=None,
        source_commit=None,
        patch_ref=None,
    )


def evaluate(evidence: tuple[GateEvidence, ...]) -> VerificationVerdict:
    """Preview consistency only; unsigned records cannot establish VERIFIED."""
    if len(evidence) > 8:
        return _inconclusive()
    try:
        evidence = tuple(GateEvidence.model_validate(r.model_dump()) for r in evidence)
    except ValidationError:
        return _inconclusive()
    by_gate: dict[GateName, GateEvidence] = {}
    for record in evidence:
        if record.gate in by_gate or PHASE_BY_GATE[record.gate] != record.phase:
            return _inconclusive()
        by_gate[record.gate] = record
    missing = tuple(
        gate
        for gate in REQUIRED_GATES
        if gate not in by_gate or by_gate[gate].status == "MISSING"
    )
    failed = tuple(
        gate
        for gate in (*REQUIRED_GATES, "STATIC_ANALYSIS_PASS")
        if gate in by_gate and by_gate[gate].status == "FAIL"
    )
    if missing or any(record.status == "MISSING" for record in by_gate.values()):
        return _inconclusive(missing=missing, failed=failed)
    if failed:
        return VerificationVerdict(
            status="NOT_VERIFIED",
            missing=(),
            failed=failed,
            workspace_sha256=None,
            source_commit=None,
            patch_ref=None,
        )
    source_commits = {record.source_commit for record in by_gate.values()}
    patch_refs = {record.patch_ref for record in by_gate.values()}
    if len(source_commits) != 1 or len(patch_refs) != 1:
        return _inconclusive()
    patch_ref = next(iter(patch_refs))
    if patch_ref is None:
        return _inconclusive()
    return VerificationVerdict(
        status="GATES_SATISFIED",
        missing=(),
        failed=(),
        # A phase may use a distinct materialized workspace. Report the
        # candidate workspace for the compact preview; the authenticated
        # context retains every phase-specific workspace binding.
        workspace_sha256=by_gate["PATCHED_REGRESSION_PASS"].workspace_sha256,
        source_commit=next(iter(source_commits)),
        patch_ref=patch_ref,
    )
