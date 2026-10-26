"""Actual PostgreSQL handoffs with a tiny source and synthetic model/receipts."""

import hashlib
import json
import os
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from packages.contracts.context import ContextBundle, ContextFile
from packages.contracts.run import Stage
from packages.providers.nebius import NebiusConfig
from services.orchestrator.baseline_delivery import receive_baseline
from services.orchestrator.patch_worker import tick_patch
from services.persistence.patch import PatchJournal
from services.persistence.store import Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_baseline_delivery import prepared
from tests.unit.test_model_dispatch import policy

store = test_postgres.store


def model_response():
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "replacements": {"src/a.py": "x=2\n"},
                                "justification": "synthetic",
                                "expected_test_delta": "assertion passes",
                                "risk_notes": "not executed",
                            }
                        )
                    },
                }
            ]
        },
    )


def setup(store, *, invalid=False):
    journal, lease, owner, run, action, signed, xml, verifier = prepared(
        store, invalid=invalid
    )
    content = "x=1\n"
    digest = hashlib.sha256(content.encode()).hexdigest()
    bundle = ContextBundle(
        source_commit=run.source_commit,
        sanitized_errors="",
        files=(
            ContextFile(
                path="src/a.py",
                source_sha256=digest,
                sanitized_sha256=digest,
                content=content,
                parse_status="PARSED",
            ),
        ),
    )
    payload = Store._context_payload(bundle)
    context = hashlib.sha256(payload).hexdigest()
    current = store.get(run.run_id, owner).model_copy(
        update={"context_refs": ("sha256:" + context,)}
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        grant = store.repository(run.repository_id, owner)
        grant = grant.model_copy(update={"approved_paths": ("src/", "tests/")})
        conn.execute(
            "UPDATE repository_grants SET grant_json=%s WHERE id=%s",
            (Jsonb(grant.model_dump(mode="json")), run.repository_id),
        )
        conn.execute(
            "INSERT INTO context_artifacts(run_id,digest,payload) VALUES (%s,%s,%s)",
            (run.run_id, context, payload),
        )
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(current.model_dump(mode="json")), run.run_id),
        )
    result = receive_baseline(
        store,
        lease,
        owner,
        action.execution_id,
        signed,
        xml,
        verifier,
        queue_patch=True,
    )
    return owner, result, lease


def test_accepted_baseline_to_patch_to_queued_verification_and_api_events(store):
    owner, run, old_lease = setup(store)
    assert run.current_stage == Stage.PATCHING
    calls = []
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic-only"})

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "replacements": {"src/a.py": "x=2\n"},
                                    "justification": "synthetic",
                                    "expected_test_delta": "assertion passes",
                                    "risk_notes": "not executed",
                                }
                            )
                        },
                    }
                ]
            },
        )

    result = tick_patch(
        store,
        uuid4(),
        config,
        policy(config),
        allow_inference=True,
        transport=httpx.MockTransport(respond),
    )
    assert result.stage == Stage.VERIFYING and result.run_id == run.run_id
    assert len(calls) == 1
    saved = store.get(run.run_id, owner)
    assert (
        saved.patch_attempt_refs
        and saved.baseline_result_ref == run.baseline_result_ref
    )
    assert PatchJournal(store).get(run.run_id, owner).proposal.files_changed == (
        "src/a.py",
    )
    assert (
        tick_patch(
            store,
            uuid4(),
            config,
            policy(config),
            allow_inference=True,
            transport=httpx.MockTransport(respond),
        ).status
        == "IDLE"
    )
    assert len(calls) == 1
    with pytest.raises(StoreError):
        store.claimed_run(old_lease)
    next_lease = store.claim(uuid4(), stages=(Stage.VERIFYING,), run_id=run.run_id)
    assert next_lease is not None
    events = store.events(run.run_id, owner)
    assert events[-1].stage == Stage.VERIFYING
    assert "x=2" not in json.dumps([e.model_dump(mode="json") for e in events])
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from services.api.app import LocalAuth, create_app

    token = "synthetic-workbench-test-token-" * 2
    with TestClient(
        create_app(
            store,
            LocalAuth(
                token=SecretStr(token),
                owner_id=owner,
                approved_repositories=(saved.repository_id,),
            ),
        ),
        client=("127.0.0.1", 1),
    ) as client:
        headers = {"Authorization": "Bearer " + token}
        assert (
            client.get(f"/api/v1/runs/{run.run_id}", headers=headers).json()[
                "current_stage"
            ]
            == "VERIFYING"
        )
        patch = client.get(f"/api/v1/runs/{run.run_id}/patch", headers=headers)
        assert (
            patch.status_code == 200
            and "+x=2" in patch.json()["proposal"]["unified_diff"]
        )


def test_invalid_reproduction_never_queues_patch_or_calls_model(store):
    _, run, _ = setup(store, invalid=True)
    assert run.current_stage == Stage.INCONCLUSIVE
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic-only"})
    result = tick_patch(
        store,
        uuid4(),
        config,
        policy(config),
        allow_inference=True,
        transport=httpx.MockTransport(
            lambda _: pytest.fail("invalid baseline inference")
        ),
    )
    assert result.status == "IDLE"


def test_inference_opt_in_is_checked_before_claiming_queued_patch(store):
    _, run, _ = setup(store)
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic-only"})
    with pytest.raises(StoreError, match="PATCH_INFERENCE_NOT_AUTHORIZED"):
        tick_patch(
            store,
            uuid4(),
            config,
            policy(config),
            transport=httpx.MockTransport(lambda _: pytest.fail("not authorized")),
        )
    assert store.claim(uuid4(), stages=(Stage.PATCHING,), run_id=run.run_id) is not None


@pytest.mark.parametrize("denied", [False, True])
def test_brokered_patch_rechecks_current_access_without_forwarding_credentials(
    store, denied
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from services.auth.collection import CollectionBroker, install_routes
    from services.auth.github import AuthDenied
    from services.orchestrator.source_broker import (
        BrokerClient,
        BrokerError,
        dispatch_patch_once,
    )

    owner, run, _ = setup(store)
    authorizations, model_calls, denials = [], [], []

    class Access:
        allow_test = True

        def authorize(self, who, repository):
            authorizations.append((who, repository))
            if denied:
                raise AuthDenied("TEST_REVOKED")

        @property
        def journal(self):
            return self

        def denial(self, kind):
            denials.append(kind)

    broker = CollectionBroker(
        store, Access(), SecretStr("s" * 43), source_loader_factory=lambda *_: None
    )
    app = FastAPI()
    install_routes(app, broker)
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic-only"})

    def infer(request):
        model_calls.append(request)
        assert "Bearer " + "s" * 43 not in request.content.decode()
        return model_response()

    with TestClient(app) as api:

        def handle(request):
            response = api.request(
                request.method,
                request.url.path,
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(response.status_code, content=response.content)

        with BrokerClient(
            "http://127.0.0.1:8000",
            SecretStr("s" * 43),
            transport=httpx.MockTransport(handle),
        ) as client:
            if denied:
                with pytest.raises(BrokerError):
                    dispatch_patch_once(
                        store,
                        uuid4(),
                        client,
                        config,
                        policy(config),
                        transport=httpx.MockTransport(infer),
                    )
            else:
                result = dispatch_patch_once(
                    store,
                    uuid4(),
                    client,
                    config,
                    policy(config),
                    transport=httpx.MockTransport(infer),
                )
                assert result.stage == Stage.VERIFYING
    assert authorizations == [(owner, run.repository_id)]
    assert len(model_calls) == (0 if denied else 1)
    assert denials == (["AUTH_PATCH_DENIED"] if denied else [])


def test_uncertain_patch_submission_is_not_repeated_after_worker_restart(store):
    from packages.providers.nebius import ProbeError

    owner, run, _ = setup(store)
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic-only"})
    calls = []

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(ProbeError):
        tick_patch(
            store,
            uuid4(),
            config,
            policy(config),
            allow_inference=True,
            transport=httpx.MockTransport(unavailable),
        )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE job_leases SET leased_until=clock_timestamp()-interval '1 second' "
            "WHERE run_id=%s",
            (run.run_id,),
        )
    with pytest.raises(StoreError, match="PATCH_SUBMISSION_UNCERTAIN"):
        tick_patch(
            Store(store.dsn),
            uuid4(),
            config,
            policy(config),
            allow_inference=True,
            transport=httpx.MockTransport(unavailable),
        )
    assert len(calls) == 1
    assert store.get(run.run_id, owner).current_stage == Stage.PATCHING
