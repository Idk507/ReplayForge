"""Correlate authenticated candidate/replay reports; no execution or stage writes."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.execution import TestCommandPlan
from packages.contracts.run import ArtifactID
from services.runner.receipts import (
    ReceiptDenied,
    ReceiptVerifier,
    SignedReplayInspection,
    SignedReplayReceipt,
)


class ReplayActions(BaseModel):
    """Expectations loaded by trusted code from dispatched actions, not reports.

    Different operation IDs correlate separate dispatches. The collector must
    independently establish fresh compute; this object cannot attest isolation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    candidate_plan: TestCommandPlan
    clean_plan: TestCommandPlan
    baseline_execution_id: UUID
    candidate_execution_id: UUID
    clean_execution_id: UUID
    patched_tree_ref: ArtifactID
    test_name: str = Field(pattern=r"^test_[A-Za-z0-9_]{1,123}$")


class ReplayReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    signed: SignedReplayReceipt
    junit: bytes = Field(max_length=131072, repr=False)


def inspect_replay_pair(
    actions: ReplayActions,
    candidate: ReplayReport,
    clean: ReplayReport,
    verifier: ReceiptVerifier,
    *,
    now: datetime,
) -> tuple[SignedReplayInspection, SignedReplayInspection]:
    """Authenticate both reports against identical immutable replay inputs.

    Failed process/tests remain explicit rejected inspections; mismatched or
    unauthenticated observations raise a safe denial. No VERIFIED status or
    capability/PR authority is produced, even when both inspections match.
    """
    actions = ReplayActions.model_validate(actions.model_dump())
    candidate = ReplayReport.model_validate(candidate.model_dump())
    clean = ReplayReport.model_validate(clean.model_dump())
    before, after = actions.candidate_plan, actions.clean_plan
    if (
        before.request.phase != "CANDIDATE"
        or before.command_id != after.command_id
        or after.request.phase != "CLEAN_REPLAY"
        or before.request.model_dump(exclude={"phase"})
        != after.request.model_dump(exclude={"phase"})
        or len(
            {
                actions.baseline_execution_id,
                actions.candidate_execution_id,
                actions.clean_execution_id,
            }
        )
        != 3
    ):
        raise ReceiptDenied("REPLAY_PAIR_BINDING_INVALID")
    return (
        verifier.inspect_replay(
            candidate.signed,
            before,
            candidate.junit,
            execution_id=actions.candidate_execution_id,
            source_tree_ref=actions.patched_tree_ref,
            test_name=actions.test_name,
            now=now,
        ),
        verifier.inspect_replay(
            clean.signed,
            after,
            clean.junit,
            execution_id=actions.clean_execution_id,
            source_tree_ref=actions.patched_tree_ref,
            test_name=actions.test_name,
            now=now,
        ),
    )
