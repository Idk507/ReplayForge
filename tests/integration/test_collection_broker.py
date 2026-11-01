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


def prepared(store, incident="Small README demonstration", path_lister=None):
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
            import hashlib

            blob = hashlib.sha1(
                f"blob {len(body)}\0".encode() + body, usedforsecurity=False
            ).hexdigest()
            f = FileIdentity(paths[0], blob, sha256(body).hexdigest(), len(body))
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
        store,
        access,
        SecretStr("s" * 43),
        source_loader_factory=factory,
        path_lister=path_lister,
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


def test_collection_rechecks_consent_after_admission_before_source_io(store):
    c, _journal, _session, broker, payload, fetched = prepared(store)
    original_authorize = broker.access.authorize
    authorization_checks = 0

    def revoke_after_first_check(owner, repository_id, *, write=False):
        nonlocal authorization_checks
        authorization_checks += 1
        if authorization_checks == 2:
            # Simulate disconnect at the fresh check immediately before source
            # I/O, after request admission and graph scope checks have passed.
            broker.access.disconnect(owner, repository_id)
        original_authorize(owner, repository_id, write=write)

    broker.access.authorize = revoke_after_first_check
    response = c.post(
        "/internal/v1/collection",
        json=payload,
        headers={"authorization": "Bearer " + "s" * 43},
    )

    assert response.status_code == 403
    assert authorization_checks == 2
    assert fetched == []


def test_worker_claim_http_broker_and_persisted_stage_agree(store):
    import httpx
    from fastapi.testclient import TestClient

    from services.orchestrator.source_broker import BrokerClient, dispatch_once

    c, j, session, broker, payload, fetched = prepared(store)
    with j.store._connection() as db:
        db.execute(
            "UPDATE job_leases SET status='queued',token=NULL,attempt_count=0 "
            "WHERE run_id=%s",
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


def test_restricted_worker_claims_and_broker_persists_events(store):
    """The worker identity has no access to source or auth custody tables."""
    import os

    import httpx
    import psycopg
    from fastapi.testclient import TestClient
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from services.orchestrator.source_broker import BrokerClient, dispatch_once
    from services.persistence.store import Store

    c, journal, session, _broker, payload, fetched = prepared(store)
    run_id = payload["lease"]["run_id"]
    role = "rf_collection_" + uuid4().hex
    admin_dsn = os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        password = uuid4().hex + uuid4().hex
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        try:
            # A CREATEROLE creator must inherit the role to drop its objects later.
            admin.execute(
                sql.SQL("GRANT {} TO CURRENT_USER WITH INHERIT TRUE").format(
                    sql.Identifier(role)
                )
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(role)
                )
            )
            admin.execute(
                sql.SQL("GRANT SELECT, UPDATE ON runs,job_leases TO {}").format(
                    sql.Identifier(role)
                )
            )
            admin.execute(
                sql.SQL("GRANT SELECT, INSERT ON run_events TO {}").format(
                    sql.Identifier(role)
                )
            )
            admin.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT ON collection_dispatches,"
                    "collection_dispatch_events TO {}"
                ).format(sql.Identifier(role))
            )
            with journal.store._connection() as db:
                db.execute(
                    "UPDATE job_leases SET status='queued',token=NULL,attempt_count=0 "
                    "WHERE run_id=%s",
                    (run_id,),
                )
            conninfo = conninfo_to_dict(admin_dsn)
            conninfo["user"] = role
            conninfo["password"] = password
            worker_store = Store(make_conninfo("", **conninfo))
            internal = TestClient(
                c.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
            )

            def handle(req):
                response = internal.request(
                    req.method,
                    str(req.url),
                    headers=dict(req.headers),
                    content=req.content,
                )
                return httpx.Response(response.status_code, content=response.content)

            with BrokerClient(
                "http://127.0.0.1:8000",
                SecretStr("s" * 43),
                transport=httpx.MockTransport(handle),
            ) as client:
                result = dispatch_once(worker_store, uuid4(), client, ("README.md",))
            assert result.stage.value == "TRIAGING" and len(fetched) == 1
            events = store.events(result.run_id, session.user_id)
            assert any(e.event_type == "LEASE_CLAIMED" for e in events)
            assert any(e.stage.value == "TRIAGING" for e in events)
            with psycopg.connect(make_conninfo("", **conninfo)) as restricted:
                assert restricted.execute(
                    "SELECT has_table_privilege(current_user,"
                    "'auth_credentials','SELECT')"
                ).fetchone() == (False,)
        finally:
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_expired_claim_is_not_redispatched_after_restart(store):
    import httpx

    from services.orchestrator.source_broker import BrokerClient, dispatch_once

    _c, journal, _session, _broker, payload, fetched = prepared(store)
    with journal.store._connection() as db:
        db.execute(
            "UPDATE job_leases SET leased_until=clock_timestamp()-interval '1 second' "
            "WHERE run_id=%s",
            (payload["lease"]["run_id"],),
        )
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"enabled": True})

    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(handle),
    ) as client:
        assert dispatch_once(store, uuid4(), client, ("README.md",)).status == "IDLE"
        assert dispatch_once(store, uuid4(), client, ("README.md",)).status == "IDLE"
    assert calls == ["GET", "GET"] and not fetched


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


def test_materialization_source_is_fetched_by_manifest_after_fresh_access(store):
    import httpx
    from pydantic import SecretStr

    from packages.contracts.run import Stage
    from services.orchestrator.authorized_materialization import (
        fetch_authorized_source,
    )
    from services.orchestrator.source_broker import BrokerClient

    c, j, session, broker, payload, fetched = prepared(store)
    response = c.post(
        "/internal/v1/collection",
        json=payload,
        headers={"authorization": "Bearer " + "s" * 43},
    )
    assert response.status_code == 200
    lease = store.claim(uuid4(), triage_only=True, stages=(Stage.TRIAGING,))
    assert lease is not None
    _, current = store.claimed_run(lease)
    current = store.advance(lease, Stage.REPRODUCING, expected_version=current.version)

    def handle(request):
        response = c.request(
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
        manifest, files = fetch_authorized_source(
            store,
            client,
            lease,
            session.user_id,
            expected_version=current.version,
        )
    assert manifest.source_commit == current.source_commit
    assert fetched == [("README.md",), ("README.md",)]
    assert files == {"README.md": b"# Small trusted synthetic demo text\n"}
    broker.access.disconnect(session.user_id, current.repository_id)
    body = {
        "lease": lease.model_dump(mode="json"),
        "expected_version": current.version,
    }
    denied = c.post(
        "/internal/v1/collection/materialization-source",
        json=body,
        headers={"authorization": "Bearer " + "s" * 43},
    )
    assert denied.status_code == 403
    assert len(fetched) == 2


def lister_returning(tree, calls):
    def lister(_token, _run, _grant):
        calls.append(1)
        return tree

    return lister


TREE = ("README.md", "src/checkout.py", "src/ledger.py", "src/web.py")


def post_collection(c, payload):
    return c.post(
        "/internal/v1/collection",
        json=payload,
        headers={"authorization": "Bearer " + "s" * 43},
    )


def test_incident_without_file_names_ranks_the_verified_tree(store):
    calls = []
    c, j, session, broker, payload, fetched = prepared(
        store,
        "Customer retried the checkout and got a duplicate payment",
        path_lister=lister_returning(TREE, calls),
    )
    r = post_collection(c, payload)
    assert r.status_code == 200 and r.json()["stage"] == "TRIAGING", r.json()
    assert calls == [1] and fetched == [("src/checkout.py",)]


def test_ranking_is_skipped_when_the_incident_names_a_file(store):
    calls = []
    c, j, session, broker, payload, fetched = prepared(
        store,
        'AssertionError in File "src/ledger.py", line 4',
        path_lister=lister_returning(TREE, calls),
    )
    assert post_collection(c, payload).status_code == 200
    assert calls == [] and fetched == [("src/ledger.py",)]


def test_unmatched_incident_keeps_the_explicit_fallback(store):
    calls = []
    c, j, session, broker, payload, fetched = prepared(
        store, "zzz qqq", path_lister=lister_returning(TREE, calls)
    )
    assert post_collection(c, payload).status_code == 200
    assert calls == [1] and fetched == [("README.md",)]


def test_ranked_paths_outside_the_grant_are_never_fetched(store):
    calls = []
    outside = ("README.md", "secrets/checkout.py")
    c, j, session, broker, payload, fetched = prepared(
        store,
        "checkout duplicate payment",
        path_lister=lister_returning(outside, calls),
    )
    assert post_collection(c, payload).status_code == 200
    assert fetched == [("README.md",)]


def test_listing_failure_collects_nothing_and_leaves_the_claim(store):
    from services.orchestrator.collection import SourceInputError

    def failing(_token, _run, _grant):
        raise SourceInputError("SOURCE_UNAVAILABLE")

    c, j, session, broker, payload, fetched = prepared(
        store, "checkout duplicate payment", path_lister=failing
    )
    post_collection(c, payload)
    assert not fetched
    from uuid import UUID

    state = store.get(UUID(payload["lease"]["run_id"]), session.user_id)
    assert state.current_stage.value != "TRIAGING"
