"""Concrete prepared Nebius provider: no I/O until admitted dispatch."""

from uuid import uuid4

import httpx
import pytest

from packages.contracts.execution import TestCommandRequest
from packages.contracts.run import Stage
from packages.contracts.sandbox import RunnerError
from services.orchestrator.reproduction import bind_reproduction
from services.persistence.baseline import BaselineAction
from services.runner.baseline import FailureExpectation
from services.runner.baseline_provider import NebiusBaselineProvider
from services.runner.commands import plan_pytest
from services.runner.prepared import PreparedImageBinding
from services.runner.receipts import plan_ref
from tests.contract.test_sandbox_smoke import config
from tests.unit.test_reproduction_handoff import fixture
from tests.unit.test_runner_admission import fixture as capabilities


def inputs():
    run, grant, proposal = fixture()
    reproduction = bind_reproduction(proposal, run, grant, grant.owner_id)
    run = run.model_copy(
        update={
            "current_stage": Stage.REPRODUCING,
            "reproduction_test_ref": reproduction.artifact_ref(),
        }
    )
    _, profile, _, sign, verifier = capabilities("REMOTE_SERVICE")
    request = TestCommandRequest(
        run_id=run.run_id,
        repository_id=run.repository_id,
        source_commit=run.source_commit,
        source_manifest_ref=run.source_manifest_ref,
        reproduction_test_ref=run.reproduction_test_ref,
        image_digest=profile.image_digest,
        phase="BASELINE",
        reproduction_path=proposal.target_path,
        test_paths=(proposal.target_path,),
    )
    action = BaselineAction(
        execution_id=uuid4(),
        plan=plan_pytest(request, run, grant, grant.owner_id, save_junit_image=True),
        source_tree_ref="sha256:" + "e" * 64,
        expected=FailureExpectation(
            test_name="test_retry", assertion_marker="RF_TEST_FAILURE"
        ),
    )
    binding = PreparedImageBinding(
        plan_ref=plan_ref(action.plan),
        execution_id=action.execution_id,
        tree_ref=action.source_tree_ref,
        image_uuid=config().image_uuid,
        image_digest=profile.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
    )
    return run, reproduction, action, binding, sign(), verifier


def test_provider_constructs_stable_rest_backend_and_closes_it():
    run, reproduction, action, binding, signed, verifier = inputs()
    calls = []

    def request(req):
        calls.append(req)
        return httpx.Response(201, json={"uuid": str(uuid4())})

    provider = NebiusBaselineProvider(
        config(),
        action,
        binding,
        signed,
        verifier,
        transport=httpx.MockTransport(request),
    )
    assert provider.run_id == run.run_id and not calls
    with provider.resolve(run, reproduction) as ready:
        assert ready.action == action and not calls
        ready.backend.submit(action.plan, action.execution_id, action.source_tree_ref)
    assert len(calls) == 1
    with pytest.raises(RunnerError, match="CLOSED"):
        ready.backend.submit(action.plan, action.execution_id, action.source_tree_ref)


@pytest.mark.parametrize("fault", ["run", "source", "cancel"])
def test_provider_denies_wrong_context_before_transport(fault):
    run, reproduction, action, binding, signed, verifier = inputs()
    updates = {
        "run": {"run_id": uuid4()},
        "source": {"source_commit": "b" * 40},
        "cancel": {"cancellation_requested": True},
    }
    provider = NebiusBaselineProvider(
        config(),
        action,
        binding,
        signed,
        verifier,
        transport=httpx.MockTransport(lambda r: pytest.fail("network I/O")),
    )
    with pytest.raises(RunnerError, match="BINDING"):
        with provider.resolve(run.model_copy(update=updates[fault]), reproduction):
            pytest.fail("bad context admitted")


@pytest.mark.parametrize(
    "field",
    [
        "plan_ref",
        "tree_ref",
        "execution_id",
        "image_uuid",
        "runner_id",
        "configuration_ref",
    ],
)
def test_provider_rejects_inconsistent_operator_binding(field):
    run, reproduction, action, binding, signed, verifier = inputs()
    value = uuid4() if field.endswith("id") else "sha256:" + "f" * 64
    with pytest.raises(RunnerError, match="BINDING"):
        NebiusBaselineProvider(
            config(),
            action,
            binding.model_copy(update={field: value}),
            signed,
            verifier,
            transport=httpx.MockTransport(lambda r: pytest.fail("I/O")),
        )


def test_live_permission_and_exact_run_selection_before_claim():
    from services.orchestrator.baseline_worker import tick_prepared_baseline

    run, reproduction, action, binding, signed, verifier = inputs()
    with pytest.raises(RunnerError, match="LIVE_NOT_AUTHORIZED"):
        NebiusBaselineProvider(config(), action, binding, signed, verifier)
    provider = NebiusBaselineProvider(
        config(),
        action,
        binding,
        signed,
        verifier,
        transport=httpx.MockTransport(lambda r: pytest.fail("I/O")),
    )

    class Store:
        def claim(self, worker_id, **kwargs):
            assert kwargs == {"stages": (Stage.REPRODUCING,), "run_id": run.run_id}
            return None

    assert tick_prepared_baseline(Store(), uuid4(), provider) is None


def test_measured_baseline_cannot_use_unmeasured_provider():
    _, _, action, binding, signed, verifier = inputs()
    plan = action.plan.model_copy(
        update={
            "command_id": "PYTEST_FILES_MEASURED_V1",
            "argv": (action.plan.argv[0], "-B", *action.plan.argv[1:]),
        }
    )
    action = action.model_copy(update={"plan": plan})
    binding = binding.model_copy(update={"plan_ref": plan_ref(plan)})
    with pytest.raises(RunnerError, match="S_BASELINE_MEASURED_BACKEND_REQUIRED"):
        NebiusBaselineProvider(
            config(),
            action,
            binding,
            signed,
            verifier,
            transport=httpx.MockTransport(lambda _: pytest.fail("network I/O")),
        )
