"""Inert controller ports verify ordering; they do not attest remote execution."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.contracts.run import Stage
from packages.providers.nebius import NebiusConfig
from services.orchestrator import repair_flow
from services.persistence.store import StoreError
from tests.unit.test_command_policy import inputs


@pytest.mark.parametrize("case", ["accepted", "invalid", "denied", "resumed"])
def test_baseline_continuation_never_generates_before_acceptance(monkeypatch, case):
    run, _, owner, _ = inputs()
    if case == "resumed":
        run = run.model_copy(update={"current_stage": Stage.VERIFYING})
    state = SimpleNamespace(run=run)
    calls = []

    def accept(*args, **kwargs):
        calls.append("accept")
        if case == "denied":
            raise StoreError("BASELINE_BINDING_DENIED")
        state.run = state.run.model_copy(
            update={
                "current_stage": Stage.INCONCLUSIVE
                if case == "invalid"
                else Stage.PATCHING,
                "version": state.run.version + 1,
            }
        )
        return state.run

    def patch(*args, **kwargs):
        assert state.run.current_stage in {Stage.PATCHING, Stage.VERIFYING}
        calls.append("patch")
        return state.run

    monkeypatch.setattr(
        repair_flow, "BaselineJournal", lambda store: SimpleNamespace(accept=accept)
    )
    monkeypatch.setattr(repair_flow, "run_patch", patch)
    args = (
        SimpleNamespace(get=lambda *args: state.run),
        SimpleNamespace(run_id=run.run_id),
        owner,
        NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"}),
        uuid4(),
        b"synthetic report",
        SimpleNamespace(),
    )
    if case == "denied":
        with pytest.raises(StoreError, match="BASELINE_BINDING_DENIED"):
            repair_flow.continue_from_baseline(*args)
    else:
        repair_flow.continue_from_baseline(*args)
    assert calls == (
        ["patch"]
        if case == "resumed"
        else ["accept"]
        if case in {"invalid", "denied"}
        else ["accept", "patch"]
    )
