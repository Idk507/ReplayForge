"""Run checkpoint and reducer security properties."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.run import RunState, Stage
from packages.policies.transitions import TransitionError, transition


def state(**changes: object) -> RunState:
    data: dict[str, object] = dict(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=uuid4(),
        source_commit="a" * 40,
        policy_version="policy-1",
        trace_id=uuid4(),
    )
    data.update(changes)
    return RunState.model_validate(data)


def test_checkpoint_roundtrip_and_identity_preservation() -> None:
    original = state()
    restored = RunState.model_validate_json(original.model_dump_json())
    updated = transition(restored, Stage.VALIDATING, expected_version=0)
    assert updated.source_commit == original.source_commit
    assert updated.run_id == original.run_id
    assert updated.version == 1
    assert original.current_stage == Stage.CREATED


@pytest.mark.parametrize(
    "changes",
    [
        {"source_commit": "main"},
        {"source_commit": "a" * 39},
        {"version": True},
        {"context_refs": ["raw stdout"]},
        {"incident_text": "secret"},
        {"policy_version": ""},
    ],
)
def test_invalid_checkpoint_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        state(**changes)


def test_stale_version_rejected() -> None:
    with pytest.raises(TransitionError, match="STALE_VERSION"):
        transition(state(), Stage.VALIDATING, expected_version=1)


@pytest.mark.parametrize("target", [Stage.VERIFIED, Stage.PR_CREATING, Stage.PATCHING])
def test_stage_skip_rejected(target: Stage) -> None:
    with pytest.raises(TransitionError):
        transition(state(), target, expected_version=0)


def test_model_claim_cannot_verify_even_from_verifying() -> None:
    with pytest.raises(TransitionError, match="VERIFIER_REQUIRED"):
        transition(
            state(current_stage=Stage.VERIFYING), Stage.VERIFIED, expected_version=0
        )


@pytest.mark.parametrize(
    "terminal",
    [
        Stage.CANCELLED,
        Stage.INCONCLUSIVE,
        Stage.NOT_VERIFIED,
        Stage.POLICY_BLOCKED,
        Stage.CLOSED,
        Stage.PR_OPENED,
        Stage.PR_FAILED,
    ],
)
def test_terminal_states_cannot_restart(terminal: Stage) -> None:
    with pytest.raises(TransitionError):
        transition(state(current_stage=terminal), Stage.VALIDATING, expected_version=0)


def test_cancel_is_terminal_and_records_request() -> None:
    cancelled = transition(state(), Stage.CANCELLED, expected_version=0)
    assert cancelled.cancellation_requested
    with pytest.raises(TransitionError):
        transition(cancelled, Stage.QUEUED, expected_version=1)


def test_pending_cancellation_blocks_forward_progress() -> None:
    with pytest.raises(TransitionError, match="CANCELLATION_PENDING"):
        transition(
            state(cancellation_requested=True), Stage.VALIDATING, expected_version=0
        )


def test_unvalidated_construct_cannot_bypass_reducer() -> None:
    forged = state().model_copy(update={"source_commit": "main"})
    with pytest.raises(ValidationError):
        transition(forged, Stage.VALIDATING, expected_version=0)


def test_patch_requires_baseline_and_test_references() -> None:
    with pytest.raises(TransitionError, match="BASELINE_EVIDENCE_REQUIRED"):
        transition(
            state(current_stage=Stage.REPRODUCING), Stage.PATCHING, expected_version=0
        )


def test_verification_requires_patch_reference() -> None:
    with pytest.raises(TransitionError, match="PATCH_EVIDENCE_REQUIRED"):
        transition(
            state(current_stage=Stage.PATCHING), Stage.VERIFYING, expected_version=0
        )
