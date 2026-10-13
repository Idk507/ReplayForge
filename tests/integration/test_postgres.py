"""Real PostgreSQL tests; use scripts.test_postgres for isolated local execution."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.budget import BudgetError
from services.api.app import LocalAuth, create_app
from services.persistence.store import Store, StoreError


@pytest.fixture(scope="module")
def store() -> Store:
    dsn = os.environ.get("REPLAYFORGE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL test database")
    command.upgrade(Config("alembic.ini"), "head")
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        role = sql.Identifier(os.environ["REPLAYFORGE_TEST_ROLE"])
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        conn.execute(sql.SQL("GRANT SELECT ON repository_grants TO {}").format(role))
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT ON context_artifacts TO {}").format(role)
        )
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE ON runs,job_leases TO {}").format(role)
        )
        conn.execute(sql.SQL("GRANT SELECT,INSERT ON run_events TO {}").format(role))
    return Store(dsn)


def new_state() -> RunState:
    return RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=uuid4(),
        source_commit="a" * 40,
        policy_version="v1",
        trace_id=uuid4(),
    )


def test_create_restart_and_idempotency(store: Store) -> None:
    owner, key, state = uuid4(), uuid4(), new_state()
    assert store.create(state, owner, key, "b" * 64) == state
    assert Store(store.dsn).get(state.run_id, owner) == state
    assert store.create(state, owner, key, "b" * 64) == state
    assert len(store.events(state.run_id, owner)) == 1
    with pytest.raises(StoreError, match="IDEMPOTENCY_CONFLICT"):
        store.create(state, owner, key, "c" * 64)
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.get(state.run_id, uuid4())


def test_concurrent_create_has_one_event_and_job(store: Store) -> None:
    owner, key, state = uuid4(), uuid4(), new_state()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: store.create(state, owner, key, "b" * 64), range(4))
        )
    assert all(result == state for result in results)
    assert len(store.events(state.run_id, owner)) == 1
    with psycopg.connect(store.dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM job_leases WHERE run_id=%s", (state.run_id,)
        ).fetchone() == (1,)


def test_transaction_rollback_discards_all_creation(store: Store) -> None:
    owner, key, state = uuid4(), uuid4(), new_state()
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """ALTER TABLE run_events ADD CONSTRAINT reject_test
            CHECK (event_type <> 'CREATED') NOT VALID"""
        )
    try:
        with pytest.raises(StoreError, match="DB_FAILURE"):
            store.create(state, owner, key, "b" * 64)
        with pytest.raises(StoreError, match="NOT_FOUND"):
            store.get(state.run_id, owner)
    finally:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute("ALTER TABLE run_events DROP CONSTRAINT reject_test")


def test_cancel_atomic_and_replay_cursor(store: Store) -> None:
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    cancelled = store.cancel(state.run_id, owner, expected_version=0)
    assert cancelled.current_stage == Stage.CANCELLED
    events = store.events(state.run_id, owner, after=1)
    assert len(events) == 1 and events[0].sequence == 2
    assert events[0].trace_id == state.trace_id
    with pytest.raises(StoreError, match="STALE_VERSION"):
        store.cancel(state.run_id, owner, expected_version=0)


def test_expired_lease_recovered_and_old_worker_fenced(store: Store) -> None:
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    # Restrict unrelated jobs for this scenario without changing application policy.
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE job_leases SET status='done' WHERE run_id<>%s", (state.run_id,)
        )
    old = store.claim(uuid4())
    assert old is not None and old.run_id == state.run_id
    assert store.claim(uuid4()) is None
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET
            leased_until=clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    recovered = Store(store.dsn).claim(uuid4())
    assert recovered is not None and recovered.token != old.token
    with pytest.raises(StoreError, match="LEASE_LOST"):
        store.advance(old, Stage.VALIDATING, expected_version=0)
    changed = store.advance(recovered, Stage.VALIDATING, expected_version=0)
    assert changed.version == 1
    assert len(store.events(state.run_id, owner)) == 4
    with pytest.raises(StoreError, match="STALE_VERSION"):
        store.advance(recovered, Stage.QUEUED, expected_version=0)


def test_cancelled_job_rejects_worker_commit(store: Store) -> None:
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE job_leases SET status='done' WHERE run_id<>%s", (state.run_id,)
        )
    lease = store.claim(uuid4())
    assert lease is not None
    store.cancel(state.run_id, owner, expected_version=0)
    with pytest.raises(StoreError, match="LEASE_LOST"):
        store.advance(lease, Stage.VALIDATING, expected_version=0)


def test_runtime_role_cannot_rewrite_events_or_migrate(store: Store) -> None:
    for statement in (
        "DELETE FROM run_events",
        "UPDATE run_events SET sequence=999",
        "ALTER TABLE runs ADD COLUMN unsafe text",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(store.dsn) as conn:
                conn.execute(statement)


def test_worker_process_death_rolls_back_uncommitted_checkpoint(store: Store) -> None:
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    env = os.environ.copy()
    env["TEST_RUN_ID"] = str(state.run_id)
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, psycopg
conn = psycopg.connect(os.environ['REPLAYFORGE_TEST_DATABASE_URL'])
conn.execute('UPDATE runs SET version=99 WHERE id=%s', (os.environ['TEST_RUN_ID'],))
os._exit(7)
""",
        ],
        env=env,
        timeout=10,
        check=False,
    )
    assert child.returncode == 7
    assert Store(store.dsn).get(state.run_id, owner).version == 0
    with psycopg.connect(store.dsn) as conn:
        assert conn.execute(
            "SELECT version FROM runs WHERE id=%s", (state.run_id,)
        ).fetchone() == (0,)


def test_concurrent_claims_never_share_run(store: Store) -> None:
    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    for _ in range(4):
        store.create(new_state(), uuid4(), uuid4(), "b" * 64)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim(uuid4()), range(4)))
    assert all(claim is not None for claim in claims)
    assert len({claim.run_id for claim in claims if claim}) == 4


def test_three_expired_claims_exhaust_job_with_audit_event(store: Store) -> None:
    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    for _ in range(3):
        assert store.claim(uuid4()) is not None
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                """UPDATE job_leases SET leased_until=
                clock_timestamp()-interval '1 second' WHERE run_id=%s""",
                (state.run_id,),
            )
    assert store.claim(uuid4()) is None
    assert store.events(state.run_id, owner)[-1].event_type == "LEASE_EXHAUSTED"
    assert store.claim(uuid4()) is None


def test_persistent_budget_reservation_and_expired_heartbeat(store: Store) -> None:
    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    lease = store.claim(uuid4())
    assert lease is not None
    renewed = store.heartbeat(lease)
    assert renewed.leased_until >= lease.leased_until
    for version in range(2):
        result = store.reserve(lease, "research_searches", expected_version=version)
    assert (
        Store(store.dsn).get(state.run_id, owner).remaining_budget.research_searches
        == 0
    )
    with pytest.raises(BudgetError):
        store.reserve(lease, "research_searches", expected_version=2)
    assert store.get(state.run_id, owner).version == result.version
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET leased_until=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    with pytest.raises(StoreError, match="LEASE_LOST"):
        store.heartbeat(lease)


def test_local_api_real_create_cancel_and_sse_reconnect(store: Store) -> None:
    owner, repository = uuid4(), uuid4()
    token = "synthetic-local-api-test-" * 3
    auth = LocalAuth(
        token=SecretStr(token), owner_id=owner, approved_repositories=(repository,)
    )
    grant = RepositoryGrant(
        repository_id=repository,
        owner_id=owner,
        remote_url="https://github.com/example/fixture",
        commits=("a" * 40,),
        approved_paths=("src/",),
        consent=True,
    )
    Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]).register_repository(grant)
    payload = {
        "repository_id": str(repository),
        "commit_sha": "a" * 40,
        "incident_text": "synthetic-incident-never-store-raw",
        "idempotency_key": str(uuid4()),
    }
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(store, auth), client=("127.0.0.1", 1)) as http:
        response = http.post("/api/v1/runs", json=payload, headers=headers)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert (
            http.post("/api/v1/runs", json=payload, headers=headers).json()["run_id"]
            == run_id
        )
        payload["incident_text"] = "different"
        assert (
            http.post("/api/v1/runs", json=payload, headers=headers).status_code == 409
        )
        assert http.get(f"/api/v1/runs/{run_id}", headers=headers).status_code == 200
        assert (
            http.post(
                f"/api/v1/runs/{run_id}/cancel",
                json={"expected_version": 0},
                headers=headers,
            ).status_code
            == 200
        )
        stream = http.get(
            f"/api/v1/runs/{run_id}/events", headers={**headers, "Last-Event-ID": "1"}
        )
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "id: 2\n" in stream.text and "id: 1\n" not in stream.text
        assert "synthetic-incident" not in stream.text
        assert (
            http.get(
                f"/api/v1/runs/{run_id}/events",
                headers={**headers, "Last-Event-ID": "bad"},
            ).status_code
            == 400
        )
    other = LocalAuth(token=SecretStr(token), owner_id=uuid4())
    with TestClient(create_app(store, other), client=("127.0.0.1", 1)) as http:
        assert http.get(f"/api/v1/runs/{run_id}", headers=headers).status_code == 404
    with psycopg.connect(store.dsn) as conn:
        row = conn.execute(
            "SELECT checkpoint::text FROM runs WHERE id=%s", (run_id,)
        ).fetchone()
        assert row is not None and "synthetic-incident" not in row[0]


def test_concurrent_budget_reservation_has_one_commit(store: Store) -> None:
    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    lease = store.claim(uuid4())
    assert lease is not None

    def reserve_once(_: int) -> str:
        try:
            store.reserve(lease, "model_calls", expected_version=0)
            return "COMMITTED"
        except StoreError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve_once, range(2))) == [
            "COMMITTED",
            "STALE_VERSION",
        ]
    assert store.get(state.run_id, owner).remaining_budget.model_calls == 14


def test_persisted_grants_are_scoped_and_runtime_cannot_insert(store: Store) -> None:
    grant = RepositoryGrant(
        repository_id=uuid4(),
        owner_id=uuid4(),
        remote_url="https://github.com/example/fixture",
        commits=("a" * 40,),
        approved_paths=("src/",),
        consent=True,
    )
    Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]).register_repository(grant)
    assert Store(store.dsn).repository(grant.repository_id, grant.owner_id) == grant
    token = "synthetic-registry-token-" * 3
    auth = LocalAuth(
        token=SecretStr(token),
        owner_id=grant.owner_id,
        approved_repositories=(grant.repository_id,),
    )
    with TestClient(create_app(store, auth), client=("127.0.0.1", 1)) as http:
        response = http.post(
            "/api/v1/runs",
            headers={"Authorization": "Bearer " + token},
            json={
                "repository_id": str(grant.repository_id),
                "commit_sha": "b" * 40,
                "incident_text": "synthetic",
                "idempotency_key": str(uuid4()),
            },
        )
        assert response.status_code == 403
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.repository(grant.repository_id, uuid4())
    with pytest.raises(StoreError, match="DB_FAILURE_42501"):
        store.register_repository(grant.model_copy(update={"repository_id": uuid4()}))


def test_context_artifact_atomic_restart_and_owner_isolation(store: Store) -> None:
    from packages.contracts.context import ContextBundle

    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    lease = store.claim(uuid4())
    assert lease is not None
    for version, target in enumerate(
        (Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING)
    ):
        store.advance(lease, target, expected_version=version)
    bundle = ContextBundle(
        source_commit=state.source_commit, sanitized_errors="sanitized fixture"
    )
    saved = store.store_context(lease, bundle, expected_version=3)
    assert saved.current_stage == Stage.TRIAGING and saved.version == 4
    artifact = saved.context_refs[0]
    assert Store(store.dsn).context(state.run_id, owner, artifact) == bundle
    assert Store(store.dsn).get(state.run_id, owner).context_refs == (artifact,)
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.context(state.run_id, uuid4(), artifact)
    with pytest.raises(StoreError, match="LEASE_LOST"):
        store.store_context(lease, bundle, expected_version=3)
    assert store.events(state.run_id, owner)[-1].stage == Stage.TRIAGING
    token = "synthetic-artifact-access-token-" * 3
    for caller, expected in ((owner, 200), (uuid4(), 404)):
        with TestClient(
            create_app(store, LocalAuth(token=SecretStr(token), owner_id=caller)),
            client=("127.0.0.1", 1),
        ) as http:
            response = http.get(
                f"/api/v1/runs/{state.run_id}/artifacts/{artifact}",
                headers={"Authorization": "Bearer " + token},
            )
            assert response.status_code == expected
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """UPDATE context_artifacts SET expires_at=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.context(state.run_id, owner, artifact)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """UPDATE context_artifacts SET expires_at=
            clock_timestamp()+interval '1 day' WHERE run_id=%s""",
            (state.run_id,),
        )
    for statement in (
        "UPDATE context_artifacts SET payload='tamper'",
        "DELETE FROM context_artifacts",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(store.dsn) as conn:
                conn.execute(statement)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE context_artifacts SET payload=%s WHERE run_id=%s",
            (b"tamper", state.run_id),
        )
    with pytest.raises(StoreError, match="ARTIFACT_INTEGRITY"):
        store.context(state.run_id, owner, artifact)


def test_context_artifact_failure_rolls_back_checkpoint(store: Store) -> None:
    from packages.contracts.context import ContextBundle

    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    lease = store.claim(uuid4())
    assert lease is not None
    for version, target in enumerate(
        (Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING)
    ):
        store.advance(lease, target, expected_version=version)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute("""ALTER TABLE context_artifacts ADD CONSTRAINT reject_context
            CHECK (false) NOT VALID""")
    try:
        with pytest.raises(StoreError, match="DB_FAILURE"):
            store.store_context(
                lease,
                ContextBundle(
                    source_commit=state.source_commit, sanitized_errors="fixture"
                ),
                expected_version=3,
            )
        assert store.get(state.run_id, owner).version == 3
        assert not store.get(state.run_id, owner).context_refs
    finally:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute("ALTER TABLE context_artifacts DROP CONSTRAINT reject_context")


def collection_fixture(store: Store, *, consent: bool = True):
    import hashlib
    from types import MappingProxyType

    from services.repository.snapshot import FileIdentity, Snapshot

    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    grant = RepositoryGrant(
        repository_id=state.repository_id,
        owner_id=owner,
        remote_url="https://github.com/example/fixture",
        commits=(state.source_commit,),
        approved_paths=("src/",),
        consent=consent,
    )
    Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]).register_repository(grant)
    store.create(state, owner, uuid4(), "b" * 64)
    lease = store.claim(uuid4())
    assert lease is not None
    body = b"# synthetic source; never executed\nx=1\n"
    snapshot = Snapshot(
        state.source_commit,
        (
            FileIdentity(
                "src/a.py", "c" * 40, hashlib.sha256(body).hexdigest(), len(body)
            ),
        ),
        MappingProxyType({"src/a.py": body}),
    )
    return owner, state, lease, snapshot


def test_collection_graph_reaches_triage_and_replay_has_no_duplicate_effects(
    store: Store,
) -> None:
    from services.orchestrator.collection import run_collection

    owner, state, lease, snapshot = collection_fixture(store)
    result = run_collection(
        store, owner, lease, snapshot, ("src/a.py",), "synthetic error"
    )
    assert result.current_stage == Stage.TRIAGING and result.version == 4
    events = store.events(state.run_id, owner)
    assert (
        run_collection(
            Store(store.dsn), owner, lease, snapshot, ("src/a.py",), "ignored"
        )
        == result
    )
    assert store.events(state.run_id, owner) == events
    assert (
        store.context(state.run_id, owner, result.context_refs[0]).files[0].path
        == "src/a.py"
    )
    assert result.remaining_budget == state.remaining_budget


def test_collection_graph_interrupt_and_new_worker_resume(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.orchestrator.collection as module

    owner, state, old, snapshot = collection_fixture(store)
    original = module.collect

    def worker_death(*args: object, **kwargs: object):
        raise RuntimeError("synthetic worker interruption")

    monkeypatch.setattr(module, "collect", worker_death)
    with pytest.raises(RuntimeError, match="synthetic worker interruption"):
        module.run_collection(store, owner, old, snapshot, ("src/a.py",), "")
    checkpoint = store.get(state.run_id, owner)
    assert checkpoint.current_stage == Stage.COLLECTING and checkpoint.version == 3
    assert not checkpoint.context_refs
    monkeypatch.setattr(module, "collect", original)
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET leased_until=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    recovered = store.claim(uuid4())
    assert recovered is not None
    with pytest.raises(StoreError, match="LEASE_LOST"):
        module.run_collection(store, owner, old, snapshot, ("src/a.py",), "")
    result = module.run_collection(
        Store(store.dsn), owner, recovered, snapshot, ("src/a.py",), ""
    )
    assert result.version == 4 and len(result.context_refs) == 1
    with psycopg.connect(store.dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM context_artifacts WHERE run_id=%s", (state.run_id,)
        ).fetchone() == (1,)


@pytest.mark.parametrize("denied", [True, False])
def test_collection_graph_explicit_failure_routing(store: Store, denied: bool) -> None:
    from services.orchestrator.collection import run_collection

    owner, state, lease, snapshot = collection_fixture(store, consent=not denied)
    result = run_collection(
        store, owner, lease, snapshot, ("src/a.py",), "x" * 65537 if not denied else ""
    )
    assert result.current_stage == (
        Stage.POLICY_BLOCKED if denied else Stage.INCONCLUSIVE
    )
    assert result.failure_reason == ("SCOPE_DENIED" if denied else "CONTEXT_REJECTED")
    assert not result.context_refs
    assert store.claim(uuid4()) is None


def test_collection_tracing_disabled_despite_environment(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langsmith.utils import tracing_is_enabled

    import services.orchestrator.collection as module

    owner, state, lease, snapshot = collection_fixture(store)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    original = module.collect

    def guarded(*args: object, **kwargs: object):
        assert not tracing_is_enabled()
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "collect", guarded)
    assert (
        module.run_collection(
            store, owner, lease, snapshot, ("src/a.py",), ""
        ).current_stage
        == Stage.TRIAGING
    )


def test_collection_source_mismatch_and_cancel_are_closed(store: Store) -> None:
    from services.orchestrator.collection import run_collection
    from services.repository.snapshot import Snapshot

    owner, state, lease, snapshot = collection_fixture(store)
    wrong = Snapshot("b" * 40, snapshot.files, snapshot._content)
    failed = run_collection(store, owner, lease, wrong, ("src/a.py",), "")
    assert failed.failure_reason == "SOURCE_MISMATCH"
    assert failed.current_stage == Stage.POLICY_BLOCKED
    owner, state, lease, snapshot = collection_fixture(store)
    cancelled = store.cancel(state.run_id, owner, expected_version=0)
    before = store.events(state.run_id, owner)
    assert run_collection(store, owner, lease, snapshot, ("src/a.py",), "") == cancelled
    assert store.events(state.run_id, owner) == before


def test_successful_collection_is_not_reclaimed(store: Store) -> None:
    from services.orchestrator.collection import run_collection

    owner,state,lease,snapshot = collection_fixture(store)
    run_collection(store,owner,lease,snapshot,("src/a.py",),"")
    with psycopg.connect(store.dsn) as conn:
        conn.execute("""UPDATE job_leases SET leased_until=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",(state.run_id,))
    assert store.claim(uuid4()) is None
    assert store.get(state.run_id,owner).current_stage == Stage.TRIAGING


def test_migration_downgrade_and_reapply(store: Store) -> None:
    # Last scenario in this module; disposable database only.
    command.downgrade(Config("alembic.ini"), "base")
    command.upgrade(Config("alembic.ini"), "head")
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone() == (0,)
