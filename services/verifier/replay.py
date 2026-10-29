"""Correlate authenticated candidate/replay reports; no execution or stage writes."""

from datetime import datetime
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from packages.contracts.execution import TestCommandPlan
from packages.contracts.run import ArtifactID
from services.runner.receipts import (
    ReceiptDenied,
    ReceiptVerifier,
    SignedReplayInspection,
    SignedReplayReceipt,
    SignedSuiteInspection,
)


class ReplayActions(BaseModel):
    """Expectations loaded by trusted code from dispatched actions, not reports.

    Different operation IDs correlate separate dispatches. The collector must
    independently establish fresh compute; this object cannot attest isolation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    candidate_plan: TestCommandPlan
    suite_plan: TestCommandPlan | None = None
    clean_plan: TestCommandPlan
    baseline_execution_id: UUID
    candidate_execution_id: UUID
    suite_execution_id: UUID | None = None
    clean_execution_id: UUID
    patched_tree_ref: ArtifactID
    test_name: str = Field(pattern=r"^test_[A-Za-z0-9_]{1,123}$")

    @model_validator(mode="after")
    def suite_identity(self) -> "ReplayActions":
        """Keep optional legacy pair shape; bind suite data as a distinct action."""
        if (self.suite_plan is None) != (self.suite_execution_id is None):
            raise ValueError("REPLAY_SUITE_ACTION_INCOMPLETE")
        if self.suite_plan is not None:
            before, suite = self.candidate_plan, self.suite_plan
            if (
                suite.request.phase != "EXISTING_SUITE"
                or suite.command_id != before.command_id
                or suite.request.model_dump(exclude={"phase", "test_paths"})
                != before.request.model_dump(exclude={"phase", "test_paths"})
                or not set(before.request.test_paths).issubset(suite.request.test_paths)
            ):
                raise ValueError("REPLAY_SUITE_BINDING_INVALID")
        execution_ids = (
            self.baseline_execution_id,
            self.candidate_execution_id,
            *((self.suite_execution_id,) if self.suite_execution_id else ()),
            self.clean_execution_id,
        )
        if len(set(execution_ids)) != len(execution_ids):
            raise ValueError("REPLAY_EXECUTION_IDS_NOT_DISTINCT")
        return self


def canonical_actions(actions: ReplayActions) -> bytes:
    """Stable persisted encoding that preserves legacy pair reservations."""
    actions = ReplayActions.model_validate(actions.model_dump())
    body = actions.model_dump(mode="json")
    if actions.suite_plan is None:
        # Old pair reservations predate these two keys and are still recoverable.
        body.pop("suite_plan")
        body.pop("suite_execution_id")
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


class ReplayReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    operation_id: UUID
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
    try:
        actions = ReplayActions.model_validate(actions.model_dump())
    except ValidationError:
        raise ReceiptDenied("REPLAY_PAIR_BINDING_INVALID") from None
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


def inspect_existing_suite(
    actions: ReplayActions,
    suite: ReplayReport,
    verifier: ReceiptVerifier,
    *,
    now: datetime,
) -> SignedSuiteInspection:
    """Inspect only the separately reserved existing-suite operation receipt."""
    try:
        actions = ReplayActions.model_validate(actions.model_dump())
    except ValidationError:
        raise ReceiptDenied("REPLAY_SUITE_BINDING_INVALID") from None
    suite = ReplayReport.model_validate(suite.model_dump())
    if actions.suite_plan is None or actions.suite_execution_id is None:
        raise ReceiptDenied("REPLAY_SUITE_ACTION_REQUIRED")
    return verifier.inspect_existing_suite(
        suite.signed,
        actions.suite_plan,
        suite.junit,
        execution_id=actions.suite_execution_id,
        source_tree_ref=actions.patched_tree_ref,
        test_paths=actions.suite_plan.request.test_paths,
        now=now,
    )
