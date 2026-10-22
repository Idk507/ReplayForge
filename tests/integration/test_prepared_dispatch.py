"""Real journal/controller plus mocked Nebius HTTP; no cloud or guest execution."""

from uuid import uuid4

import httpx
import pytest

from services.orchestrator.replay_dispatch import PreparedTestBackend, dispatch_replay
from services.runner.prepared import PreparedImageBinding, PreparedNebiusBackend
from services.runner.receipts import plan_ref
from tests.contract.test_sandbox_smoke import config
from tests.integration import test_postgres
from tests.integration.test_replay_dispatch import inputs

store = test_postgres.store


@pytest.mark.parametrize("timeout", [False, True])
def test_real_rest_backend_composes_with_durable_dispatch_and_restart(store, timeout):
    lease, owner, run, actions, profile, sign, verifier, _ = inputs(store)
    binding = PreparedImageBinding(
        plan_ref=plan_ref(actions.candidate_plan),
        execution_id=actions.candidate_execution_id,
        tree_ref=actions.patched_tree_ref,
        image_uuid=config().image_uuid,
        image_digest=actions.candidate_plan.request.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
    )
    operation, calls = uuid4(), []

    def handler(request):
        calls.append(request)
        if timeout:
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(201, json={"uuid": str(operation)})

    with PreparedNebiusBackend(
        config(), binding, sign(), verifier, transport=httpx.MockTransport(handler)
    ) as backend:
        port: PreparedTestBackend = backend
        first = dispatch_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            port,
            expected_version=run.version,
        )
        second = dispatch_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            port,
            expected_version=run.version,
        )
    assert first.status == ("SUBMISSION_UNKNOWN" if timeout else "SUBMITTED")
    assert second.status == ("SUBMISSION_UNKNOWN" if timeout else "ALREADY_SUBMITTED")
    assert first.operation_id == second.operation_id == (None if timeout else operation)
    assert first.evidence_origin == "TEST_TRANSPORT"
    assert len(calls) == 1
