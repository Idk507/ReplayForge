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
