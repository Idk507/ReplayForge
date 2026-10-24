"""Small authenticated collection/triage flow; fake inference, real PostgreSQL."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from packages.providers.nebius import NebiusConfig
from services.orchestrator.source_broker import (
    BrokerClient,
    BrokerError,
    dispatch_triage_once,
)
from tests.integration import test_postgres
from tests.integration.test_collection_broker import prepared
from tests.unit.test_model_dispatch import policy
from tests.unit.test_triage_gateway import response_for_any_context

store = test_postgres.store


@pytest.mark.parametrize("revoked", [False, True])
def test_authenticated_queue_handoff_never_leaks_token_or_retries(store, revoked):
    c, j, session, broker, payload, fetched = prepared(store)
    assert (
        c.post(
            "/internal/v1/collection",
            json=payload,
            headers={"authorization": "Bearer " + "s" * 43},
        ).status_code
        == 200
    )
    if revoked:
        from uuid import UUID

        run = store.get(UUID(payload["lease"]["run_id"]), session.user_id)
        broker.access.disconnect(session.user_id, run.repository_id)
    internal = TestClient(
        c.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
    )

    def handle(req):
        r = internal.request(
            req.method, str(req.url), headers=dict(req.headers), content=req.content
        )
        assert "synthetic-access" not in r.text
        return httpx.Response(r.status_code, content=r.content)

    calls = []

    def infer(req):
        calls.append(req)
        return response_for_any_context()

    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"})
    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(handle),
    ) as client:
        if revoked:
            with pytest.raises(BrokerError, match="DENIED"):
                dispatch_triage_once(
                    store,
                    uuid4(),
                    client,
                    config,
                    policy(config),
                    transport=httpx.MockTransport(infer),
                )
            assert not calls
            return
        result = dispatch_triage_once(
            store,
            uuid4(),
            client,
            config,
            policy(config),
            transport=httpx.MockTransport(infer),
        )
    assert result.status in {"IDLE", "PROCESSED"}
    if revoked:
        assert not calls
    else:
        assert result.stage.value == "REPRODUCING" and len(calls) == 1
        with BrokerClient(
            "http://127.0.0.1:8000",
            SecretStr("s" * 43),
            transport=httpx.MockTransport(handle),
        ) as client:
            assert (
                dispatch_triage_once(
                    store,
                    uuid4(),
                    client,
                    config,
                    policy(config),
                    transport=httpx.MockTransport(infer),
                ).status
                == "IDLE"
            )
        assert len(calls) == 1
        from contextlib import contextmanager

        from packages.contracts.execution import TestCommandRequest
        from services.orchestrator.baseline_worker import (
            PreparedBaseline,
            tick_prepared_baseline,
        )
        from services.orchestrator.worker import tick
        from services.persistence.baseline import BaselineAction
        from services.runner.baseline import FailureExpectation
        from services.runner.commands import plan_pytest
        from tests.integration.test_replay_dispatch import FakeBackend
        from tests.unit.test_runner_admission import fixture

        assert (
            tick(store, uuid4(), lambda *a: pytest.fail("reclaimed baseline")).status
            == "IDLE"
        )
        _, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
        backend = FakeBackend(profile)
        closed = []

        class Provider:
            origin = "TEST_TRANSPORT"

            @contextmanager
            def resolve(self, run, reproduction):
                grant = store.repository(run.repository_id, session.user_id)
                request = TestCommandRequest(
                    run_id=run.run_id,
                    repository_id=run.repository_id,
                    source_commit=run.source_commit,
                    source_manifest_ref=run.source_manifest_ref,
                    reproduction_test_ref=run.reproduction_test_ref,
                    image_digest=profile.image_digest,
                    phase="BASELINE",
                    reproduction_path=reproduction.proposal.target_path,
                    test_paths=(reproduction.proposal.target_path,),
                )
                action = BaselineAction(
                    execution_id=uuid4(),
                    plan=plan_pytest(
                        request, run, grant, session.user_id, save_junit_image=True
                    ),
                    source_tree_ref="sha256:" + "e" * 64,
                    expected=FailureExpectation(
                        test_name="test_duplicate",
                        assertion_marker="RF_DUPLICATE_TRANSACTION",
                    ),
                )
                try:
                    yield PreparedBaseline(action, sign(), verifier, backend)
                finally:
                    closed.append(True)

        dispatched = tick_prepared_baseline(store, uuid4(), Provider())
        assert dispatched.status == "SUBMITTED" and len(backend.calls) == 1
        assert closed == [True]
