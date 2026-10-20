"""Versioned metadata checkpoint; construction is not verification or approval."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.budget import Budget

ArtifactID = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
CommitSHA = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class Stage(StrEnum):
    """Architecture workflow stages, distinct from probe statuses."""

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    COLLECTING = "COLLECTING"
    TRIAGING = "TRIAGING"
    REPRODUCING = "REPRODUCING"
    PATCHING = "PATCHING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PR_CREATING = "PR_CREATING"
    PR_OPENED = "PR_OPENED"
    PR_FAILED = "PR_FAILED"
    CLOSED = "CLOSED"
    NOT_VERIFIED = "NOT_VERIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    CANCELLED = "CANCELLED"


class RunState(BaseModel):
    """Immutable bounded metadata; raw logs and incident bodies are excluded.

    This initial contract deliberately stores artifact references for outputs.
    Persistence, authorization and trusted verifier ingestion are separate gates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    incident_id: UUID
    repository_id: UUID
    source_commit: CommitSHA
    policy_version: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    trace_id: UUID
    current_stage: Stage = Stage.CREATED
    version: int = Field(default=0, strict=True, ge=0)
    context_refs: tuple[ArtifactID, ...] = Field(default=(), max_length=100)
    source_manifest_ref: ArtifactID | None = None
    source_snapshot_ref: ArtifactID | None = None
    hypothesis_refs: tuple[ArtifactID, ...] = Field(default=(), max_length=3)
    selected_hypothesis_ref: ArtifactID | None = None
    reproduction_test_ref: ArtifactID | None = None
    baseline_result_ref: ArtifactID | None = None
    patch_attempt_refs: tuple[ArtifactID, ...] = Field(default=(), max_length=3)
    verification_ref: ArtifactID | None = None
    evidence_manifest_ref: ArtifactID | None = None
    research_refs: tuple[ArtifactID, ...] = Field(default=(), max_length=2)
    cancellation_requested: bool = Field(default=False, strict=True)
    remaining_budget: Budget = Field(default_factory=Budget)
    failure_reason: (
        Literal[
            "SCOPE_DENIED",
            "SOURCE_MISMATCH",
            "CONTEXT_REJECTED",
            "LEASE_EXHAUSTED",
            "SOURCE_REJECTED",
            "SOURCE_UNAVAILABLE",
            "BUDGET_EXHAUSTED",
            "INVALID_REPRODUCTION",
        ]
        | None
    ) = None
