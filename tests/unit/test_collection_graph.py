"""Collection planning is deterministic and cannot dispatch model/shell actions."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.run import RunState, Stage
from services.orchestrator.collection import CollectionDelta, plan


def state(stage: Stage) -> RunState:
    return RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=uuid4(),
        source_commit="a" * 40,
        policy_version="v1",
        trace_id=uuid4(),
        current_stage=stage,
    )


@pytest.mark.parametrize(
    "stage,target",
    [
        (Stage.CREATED, Stage.VALIDATING),
        (Stage.VALIDATING, Stage.QUEUED),
        (Stage.QUEUED, Stage.COLLECTING),
    ],
)
def test_early_stage_plans_have_no_context(stage: Stage, target: Stage) -> None:
    delta = plan(state(stage))
    assert delta.target == target and delta.context is None


def test_privileged_or_unbound_deltas_rejected() -> None:
    with pytest.raises(ValidationError):
        CollectionDelta(target=Stage.VERIFIED)
    with pytest.raises(ValidationError):
        CollectionDelta(target=Stage.TRIAGING)
