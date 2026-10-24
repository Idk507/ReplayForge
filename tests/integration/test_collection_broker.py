"""Authenticated collection dispatch; synthetic source, real PostgreSQL fences."""

from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from uuid import uuid4

import pytest
from pydantic import SecretStr

from packages.contracts.source import SourceFile, SourceManifest
from services.auth.collection import CollectionBroker, CollectionRequest, install_routes
from services.orchestrator.collection import CollectionInputs
from services.repository.snapshot import FileIdentity, Snapshot
from tests.integration import test_postgres
from tests.integration.test_session_access import connect, setup

store = test_postgres.store


def prepared(store, incident="Small README demonstration"):
    c, j, session, headers, access, calls = setup(store)
    repo = connect(access, session)
    with j.store._connection() as db:
        db.execute("UPDATE job_leases SET status='done'")
    r = c.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "repository_id": str(repo.repository_id),
            "commit_sha": "a" * 40,
            "incident_text": incident,
            "idempotency_key": str(uuid4()),
        },
    )
    assert r.status_code == 202
    lease = store.claim(uuid4())
    assert lease is not None
    _, state = store.claimed_run(lease)
    fetched = []

    def factory(token, paths):
        def load(run, grant):
            fetched.append(paths)
            body = b"# Small trusted synthetic demo text\n"
            f = FileIdentity(paths[0], "b" * 40, sha256(body).hexdigest(), len(body))
            snap = Snapshot(
                "a" * 40,
                (f,),
                MappingProxyType({paths[0]: body}),
                evidence_origin="TEST_TRANSPORT",
                commit_identity="GITHUB_HTTPS_METADATA",
            )
            manifest = SourceManifest(
                repository_id=run.repository_id,
                source_commit=run.source_commit,
                root_tree="b" * 40,
                evidence_origin="TEST_TRANSPORT",
                observed_at=datetime.now(UTC),
                trace_id=run.trace_id,
                files=(
                    SourceFile(
                        path=f.path,
                        blob_id=f.blob_id,
                        sha256=f.sha256,
                        size_bytes=f.size_bytes,
                    ),
                ),
            )
            return CollectionInputs(
                snap, paths, known_secrets=(token,), source_manifest=manifest
            )

        return load

    broker = CollectionBroker(
        store, access, SecretStr("s" * 43), source_loader_factory=factory
    )
    install_routes(c.app, broker)
    payload = CollectionRequest(
        lease=lease, expected_version=state.version, paths=("README.md",)
    ).model_dump(mode="json")
    return c, j, session, broker, payload, fetched


def test_collection_broker_retains_context_without_returning_credentials(store):
    c, j, session, broker, payload, fetched = prepared(store)
    r = c.post(
        "/internal/v1/collection",
        json=payload,
        headers={"authorization": "Bearer " + "s" * 43},
    )
    assert r.status_code == 200 and r.json()["stage"] == "TRIAGING", r.json()
    assert fetched == [("README.md",)]
    assert "synthetic-access" not in r.text and "secret" not in r.text
    from uuid import UUID

    state = store.get(UUID(payload["lease"]["run_id"]), session.user_id)
    assert state.context_refs and state.current_stage.value == "TRIAGING"
    assert c.get("/api/v1/runs/" + str(state.run_id) + "/events").status_code == 200
    assert (
        c.post(
            "/internal/v1/collection",
            json=payload,
            headers={"authorization": "Bearer " + "s" * 43},
        ).status_code
        == 409
    )
    assert len(fetched) == 1


def test_incident_reference_selects_relevant_file_before_source_read(store):
    c, j, session, broker, payload, fetched = prepared(
        store, 'AssertionError in File "src/checkout.py", line 12'
    )
    r = c.post(
        "/internal/v1/collection",
        json=payload,
        headers={"authorization": "Bearer " + "s" * 43},
    )
    assert r.status_code == 200 and r.json()["stage"] == "TRIAGING"
    assert fetched == [("src/checkout.py",)]


@pytest.mark.parametrize(
    "fault", ["key", "browser", "lease", "version", "path", "consent"]
)
def test_collection_broker_denies_before_source_fetch(store, fault):
    c, j, session, broker, payload, fetched = prepared(store)
    headers = {"authorization": "Bearer " + "s" * 43}
    if fault == "key":
        headers["authorization"] = "Bearer " + "x" * 43
    if fault == "browser":
        headers["origin"] = "http://127.0.0.1:3000"
    if fault == "lease":
        payload["lease"]["token"] = str(uuid4())
    if fault == "version":
        payload["expected_version"] += 1
    if fault == "path":
        payload["paths"] = [".env"]
    if fault == "consent":
        from uuid import UUID

        state = store.get(UUID(payload["lease"]["run_id"]), session.user_id)
        broker.access.disconnect(session.user_id, state.repository_id)
    r = c.post("/internal/v1/collection", json=payload, headers=headers)
    assert r.status_code in {403, 409, 422}
    assert not fetched


def test_worker_claim_http_broker_and_persisted_stage_agree(store):
    import httpx
    from fastapi.testclient import TestClient

    from services.orchestrator.source_broker import BrokerClient, dispatch_once

    c, j, session, broker, payload, fetched = prepared(store)
    with j.store._connection() as db:
        db.execute(
            "UPDATE job_leases SET status='queued',token=NULL WHERE run_id=%s",
            (payload["lease"]["run_id"],),
        )
    internal = TestClient(
        c.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
    )

    def handle(req):
        r = internal.request(
            req.method, str(req.url), headers=dict(req.headers), content=req.content
        )
        return httpx.Response(r.status_code, content=r.content)

    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(handle),
    ) as client:
        result = dispatch_once(store, uuid4(), client, ("README.md",))
    assert result.stage.value == "TRIAGING" and len(fetched) == 1


def test_cancelled_collection_never_fetches_source(store):
    c, j, session, broker, payload, fetched = prepared(store)
    from uuid import UUID

    store.cancel(
        UUID(payload["lease"]["run_id"]),
        session.user_id,
        expected_version=payload["expected_version"],
    )
    r = c.post(
        "/internal/v1/collection",
        json=payload,
        headers={"authorization": "Bearer " + "s" * 43},
    )
    assert r.status_code == 409 and not fetched
