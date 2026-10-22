"""Bounded historical observations; recall never authorizes a new repair."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts.gates import GateName
from packages.contracts.run import ArtifactID, CommitSHA


class RepairLesson(BaseModel):
    """Deterministic lesson card with no model-authored text, code or raw logs."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    source_run_id: UUID
    source_commit: CommitSHA
    patch_ref: ArtifactID
    verification_ref: ArtifactID
    manifest_ref: ArtifactID
    outcome: Literal["VERIFIED", "NOT_VERIFIED", "INCONCLUSIVE"]
    failed_gates: tuple[GateName, ...] = Field(max_length=8)
    missing_gates: tuple[GateName, ...] = Field(max_length=8)
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    instruction: Literal["HISTORICAL_ONLY_REPRODUCE_AND_VERIFY_AGAIN"] = (
        "HISTORICAL_ONLY_REPRODUCE_AND_VERIFY_AGAIN"
    )


class MemoryRecall(BaseModel):
    """Private bounded tool result, derived from retained authenticated outcomes."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    status: Literal["RECALLED", "EMPTY"]
    run_id: UUID
    repository_id: UUID
    lessons: tuple[RepairLesson, ...] = Field(max_length=3)
    observed_at: AwareDatetime
    trace_id: UUID
    policy_decision: Literal["ALLOW_SCOPED_ADVISORY_MEMORY"] = (
        "ALLOW_SCOPED_ADVISORY_MEMORY"
    )
    artifacts: tuple[ArtifactID, ...] = Field(max_length=3)
    retryable: Literal[False] = False
    error_class: None = None
    can_authorize_execution: Literal[False] = False
    can_authorize_verification: Literal[False] = False
