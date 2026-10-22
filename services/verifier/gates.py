"""Minimal deterministic gate evaluator for structured execution evidence."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.gates import GateName as GateName
from packages.contracts.run import ArtifactID, CommitSHA

REQUIRED_GATES: tuple[GateName, ...] = (
    "BASELINE_REPRODUCED",
    "PATCHED_REGRESSION_PASS",
    "EXISTING_SUITE_PASS",
    "CLEAN_REPLAY_PASS",
    "PATCH_SCOPE_PASS",
    "ARTIFACT_INTEGRITY_PASS",
    "SECURITY_GATE_PASS",
)


class GateEvidence(BaseModel):
    """One prevalidated deterministic observation, never a model assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    gate: GateName
    status: Literal["PASS", "FAIL", "MISSING"]
    evidence_origin: Literal["REMOTE_SERVICE", "TRUSTED_VERIFIER"]
    execution_ref: ArtifactID
    execution_id: UUID | None = None
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
        if record.gate in by_gate:
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
    workspaces = {record.workspace_sha256 for record in by_gate.values()}
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
    if len(workspaces) != 1 or len(source_commits) != 1 or len(patch_refs) != 1:
        return _inconclusive()
    patch_ref = next(iter(patch_refs))
    if patch_ref is None:
        return _inconclusive()
    return VerificationVerdict(
        status="GATES_SATISFIED",
        missing=(),
        failed=(),
        workspace_sha256=next(iter(workspaces)),
        source_commit=next(iter(source_commits)),
        patch_ref=patch_ref,
    )
