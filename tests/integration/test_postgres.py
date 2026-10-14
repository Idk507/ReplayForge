"""Real PostgreSQL tests; use scripts.test_postgres for isolated local execution."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr

from packages.contracts.incident import IncidentReport
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
            sql.SQL("GRANT SELECT,INSERT ON source_manifests TO {}").format(role)
        )
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT ON sealed_snapshots TO {}").format(role)
        )
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT ON incident_reports TO {}").format(role)
        )
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


@pytest.mark.parametrize(
    "stage", [Stage.CREATED, Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING]
)
def test_three_expired_claims_exhaust_job_with_audit_event(
    store: Store, stage: Stage
) -> None:
    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    for attempt in range(3):
        lease = store.claim(uuid4())
        assert lease is not None
        if attempt == 0:
            for target in (Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING):
                if state.current_stage == stage:
                    break
                state = store.advance(lease, target, expected_version=state.version)
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                """UPDATE job_leases SET leased_until=
                clock_timestamp()-interval '1 second' WHERE run_id=%s""",
                (state.run_id,),
            )
    assert store.claim(uuid4()) is None
    assert store.events(state.run_id, owner)[-1].event_type == "LEASE_EXHAUSTED"
    exhausted = store.get(state.run_id, owner)
    assert exhausted.current_stage == Stage.INCONCLUSIVE
    assert exhausted.failure_reason == "LEASE_EXHAUSTED"
    assert exhausted.version == state.version + 1
    events = store.events(state.run_id, owner)
    assert store.claim(uuid4()) is None
    assert store.events(state.run_id, owner) == events
    with pytest.raises(StoreError, match="LEASE_LOST"):
        store.heartbeat(lease)


def test_cancellation_before_exhaustion_preserves_cancelled_result(
    store: Store,
) -> None:
    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    lease = store.claim(uuid4())
    assert lease is not None
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET attempt_count=3,
            leased_until=clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    cancelled = store.cancel(state.run_id, owner, expected_version=0)
    assert store.claim(uuid4()) is None
    assert store.get(state.run_id, owner) == cancelled
    assert cancelled.failure_reason is None
    assert store.events(state.run_id, owner)[-1].event_type == "CANCELLED"


def test_exhaustion_event_failure_rolls_back_checkpoint_and_fence(store: Store) -> None:
    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "b" * 64)
    lease = store.claim(uuid4())
    assert lease is not None
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET attempt_count=3,
            leased_until=clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    before = store.events(state.run_id, owner)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """ALTER TABLE run_events ADD CONSTRAINT reject_exhaustion
            CHECK (event_type <> 'LEASE_EXHAUSTED') NOT VALID"""
        )
    try:
        with pytest.raises(StoreError, match="DB_FAILURE"):
            store.claim(uuid4())
        assert store.get(state.run_id, owner) == state
        assert store.events(state.run_id, owner) == before
        with psycopg.connect(store.dsn) as conn:
            assert conn.execute(
                "SELECT status,token FROM job_leases WHERE run_id=%s", (state.run_id,)
            ).fetchone() == ("leased", lease.token)
    finally:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute("ALTER TABLE run_events DROP CONSTRAINT reject_exhaustion")
    assert store.claim(uuid4()) is None
    assert store.get(state.run_id, owner).current_stage == Stage.INCONCLUSIVE


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
        "incident_text": "synthetic-incident-never-store-raw password=synthetic-value "
        + token,
        "idempotency_key": str(uuid4()),
    }
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(store, auth), client=("127.0.0.1", 1)) as http:
        response = http.post("/api/v1/runs", json=payload, headers=headers)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        report = Store(store.dsn).incident(UUID(run_id), owner)
        assert "synthetic-incident" in report.text
        assert token not in report.text and "synthetic-value" not in report.text
        assert "synthetic-incident" not in response.text
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


@pytest.mark.parametrize("identity", ["GIT_OBJECT_HASH", "GITHUB_HTTPS_METADATA"])
def test_context_artifact_atomic_restart_and_owner_isolation(
    store: Store, identity: Literal["GIT_OBJECT_HASH", "GITHUB_HTTPS_METADATA"]
) -> None:
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
        source_commit=state.source_commit,
        sanitized_errors="sanitized fixture",
        commit_identity=identity,
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


def collection_fixture(
    store: Store, *, consent: bool = True, incident: IncidentReport | None = None
):
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
    store.create(state, owner, uuid4(), "b" * 64, incident=incident)
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

    owner, state, lease, snapshot = collection_fixture(store)
    run_collection(store, owner, lease, snapshot, ("src/a.py",), "")
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET leased_until=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    assert store.claim(uuid4()) is None
    assert store.get(state.run_id, owner).current_stage == Stage.TRIAGING


def test_worker_dispatches_one_job_and_success_stays_idle(store: Store) -> None:
    from services.orchestrator.collection import CollectionInputs
    from services.orchestrator.worker import tick

    owner, state, initial, snapshot = collection_fixture(store)
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET status='queued',token=NULL
            WHERE run_id=%s""",
            (state.run_id,),
        )
    calls: list[Stage] = []

    def loader(run: RunState, grant: RepositoryGrant) -> CollectionInputs:
        assert grant.owner_id == owner
        calls.append(run.current_stage)
        return CollectionInputs(snapshot, ("src/a.py",))

    result = tick(store, uuid4(), loader)
    assert result.stage == Stage.TRIAGING and result.status == "PROCESSED"
    assert calls == [Stage.COLLECTING]
    assert tick(store, uuid4(), loader).status == "IDLE"
    assert len(calls) == 1
    with pytest.raises(StoreError, match="LEASE_LOST"):
        store.claimed_run(initial)


def test_worker_denied_scope_never_loads_inputs(store: Store) -> None:
    from services.orchestrator.worker import tick

    owner, state, lease, snapshot = collection_fixture(store, consent=False)
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE job_leases SET status='queued',token=NULL WHERE run_id=%s",
            (state.run_id,),
        )

    def loader(run: RunState, grant: RepositoryGrant):
        pytest.fail("denied scope must not invoke the input adapter")

    assert tick(store, uuid4(), loader).stage == Stage.POLICY_BLOCKED
    assert store.get(state.run_id, owner).failure_reason == "SCOPE_DENIED"


def test_worker_input_failure_recovers_with_fresh_claim(store: Store) -> None:
    from services.orchestrator.collection import CollectionError, CollectionInputs
    from services.orchestrator.worker import tick

    owner, state, lease, snapshot = collection_fixture(store)
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE job_leases SET status='queued',token=NULL WHERE run_id=%s",
            (state.run_id,),
        )

    def unavailable(run: RunState, grant: RepositoryGrant):
        raise OSError("synthetic input service interruption")

    with pytest.raises(CollectionError, match="INPUT_UNAVAILABLE"):
        tick(store, uuid4(), unavailable)
    assert store.get(state.run_id, owner).current_stage == Stage.COLLECTING
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            """UPDATE job_leases SET leased_until=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    result = tick(
        Store(store.dsn),
        uuid4(),
        lambda run, grant: CollectionInputs(snapshot, ("src/a.py",)),
    )
    assert result.stage == Stage.TRIAGING
    assert len(store.get(state.run_id, owner).context_refs) == 1


def test_worker_loader_cannot_commit_after_lease_expiry(store: Store) -> None:
    from services.orchestrator.collection import CollectionInputs
    from services.orchestrator.worker import tick

    owner, state, lease, snapshot = collection_fixture(store)
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE job_leases SET status='queued',token=NULL WHERE run_id=%s",
            (state.run_id,),
        )

    def expires(run: RunState, grant: RepositoryGrant) -> CollectionInputs:
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                """UPDATE job_leases SET leased_until=
                clock_timestamp()-interval '1 second' WHERE run_id=%s""",
                (state.run_id,),
            )
        return CollectionInputs(snapshot, ("src/a.py",))

    with pytest.raises(StoreError, match="LEASE_LOST"):
        tick(store, uuid4(), expires)
    assert store.get(state.run_id, owner).current_stage == Stage.COLLECTING
    assert not store.get(state.run_id, owner).context_refs


def test_incident_atomic_creation_restart_scope_and_tamper(store: Store) -> None:
    from services.context.incident import prepare_incident

    owner, state, key = uuid4(), new_state(), uuid4()
    report = prepare_incident("failure password=synthetic-credential")
    store.create(state, owner, key, "c" * 64, incident=report)
    assert Store(store.dsn).incident(state.run_id, owner) == report
    assert "synthetic-credential" not in report.text
    # Matching retries never rewrite the first durable input, even if an internal
    # caller supplies a different report with the same opaque request hash.
    store.create(state, owner, key, "c" * 64, incident=prepare_incident("replacement"))
    assert store.incident(state.run_id, owner) == report
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.incident(state.run_id, uuid4())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                "DELETE FROM incident_reports WHERE run_id=%s", (state.run_id,)
            )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE incident_reports SET payload=%s WHERE run_id=%s",
            (b"tampered", state.run_id),
        )
    with pytest.raises(StoreError, match="INCIDENT_INTEGRITY"):
        store.incident(state.run_id, owner)


def test_bound_incident_survives_recovery_and_reaches_context(store: Store) -> None:
    from services.context.incident import prepare_incident
    from services.orchestrator.collection import CollectionInputs, run_collection
    from services.orchestrator.worker import bind_incident

    report = prepare_incident("duplicate transaction password=synthetic-credential")
    owner, state, lease, snapshot = collection_fixture(store, incident=report)
    # A fresh adapter simulates process reconstruction, not raw in-memory input.
    restarted = Store(store.dsn)

    def source(run: RunState, grant: RepositoryGrant) -> CollectionInputs:
        return CollectionInputs(snapshot, ("src/a.py",), "synthetic trace")

    result = run_collection(
        restarted, owner, lease, input_loader=bind_incident(restarted, source)
    )
    context = restarted.context(state.run_id, owner, result.context_refs[0])
    assert "duplicate transaction" in context.sanitized_errors
    assert "synthetic trace" in context.sanitized_errors
    assert "synthetic-credential" not in context.model_dump_json()


def test_missing_incident_blocks_source_loader(store: Store) -> None:
    from services.orchestrator.collection import CollectionError, run_collection
    from services.orchestrator.worker import bind_incident

    owner, state, lease, snapshot = collection_fixture(store)
    called = False

    def source(run: RunState, grant: RepositoryGrant):
        nonlocal called
        called = True
        raise AssertionError("must not access source without retained incident")

    with pytest.raises(CollectionError, match="INCIDENT_UNAVAILABLE"):
        run_collection(store, owner, lease, input_loader=bind_incident(store, source))
    assert not called
    assert store.get(state.run_id, owner).current_stage == Stage.COLLECTING


def test_incident_expiry_and_creation_rollback(store: Store) -> None:
    from services.context.incident import prepare_incident

    owner, state = uuid4(), new_state()
    report = prepare_incident("synthetic retained incident")
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute("""ALTER TABLE incident_reports ADD CONSTRAINT reject_incident
            CHECK (false) NOT VALID""")
    try:
        with pytest.raises(StoreError, match="DB_FAILURE"):
            store.create(state, owner, uuid4(), "d" * 64, incident=report)
        with pytest.raises(StoreError, match="NOT_FOUND"):
            store.get(state.run_id, owner)
        with psycopg.connect(store.dsn) as conn:
            assert conn.execute(
                "SELECT count(*) FROM run_events WHERE run_id=%s", (state.run_id,)
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT count(*) FROM job_leases WHERE run_id=%s", (state.run_id,)
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute("ALTER TABLE incident_reports DROP CONSTRAINT reject_incident")
    store.create(state, owner, uuid4(), "d" * 64, incident=report)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """UPDATE incident_reports SET expires_at=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.incident(state.run_id, owner)


@pytest.mark.parametrize("has_identity", [False, True])
def test_legacy_context_without_commit_identity_remains_readable(
    store: Store, has_identity: bool
) -> None:
    import hashlib
    import json

    from packages.contracts.context import ContextBundle

    owner, state = uuid4(), new_state()
    store.create(state, owner, uuid4(), "e" * 64)
    bundle = ContextBundle(source_commit=state.source_commit, sanitized_errors="legacy")
    data = bundle.model_dump(mode="json")
    if not has_identity:
        data.pop("commit_identity")
    data.pop("evidence_origin")
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO context_artifacts(run_id,digest,payload) VALUES (%s,%s,%s)",
            (state.run_id, digest, payload),
        )
    restored = store.context(state.run_id, owner, "sha256:" + digest)
    assert restored.sanitized_errors == "legacy"
    assert restored.commit_identity == "GIT_OBJECT_HASH"


@pytest.mark.parametrize("outcome", ["success", "unavailable", "tampered"])
@pytest.mark.parametrize("retain_snapshot", [False, True])
def test_api_incident_to_http_source_worker_context_pipeline(
    store: Store, outcome: str, retain_snapshot: bool
) -> None:
    import base64
    import hashlib

    import httpx
    from pydantic import SecretBytes

    from services.orchestrator.source import github_inputs
    from services.orchestrator.worker import bind_incident, tick
    from services.repository.packaging import SnapshotCipher

    with psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
    owner, repository = uuid4(), uuid4()
    auth_token = "synthetic-local-operator-" * 3
    read_token = SecretStr("synthetic-repository-read-credential")
    content = ("# synthetic data " + read_token.get_secret_value() + "\nx=1\n").encode()

    def oid(kind: str, data: bytes) -> str:
        return hashlib.sha1(f"{kind} {len(data)}\0".encode() + data).hexdigest()

    blob = oid("blob", content)
    subtree = oid("tree", b"100644 a.py\0" + bytes.fromhex(blob))
    root = oid("tree", b"40000 src\0" + bytes.fromhex(subtree))
    grant = RepositoryGrant(
        repository_id=repository,
        owner_id=owner,
        remote_url="https://github.com/example/fixture",
        commits=("a" * 40,),
        approved_paths=("src/",),
        consent=True,
    )
    Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]).register_repository(grant)
    auth = LocalAuth(
        token=SecretStr(auth_token), owner_id=owner, approved_repositories=(repository,)
    )
    headers = {"Authorization": "Bearer " + auth_token}
    with TestClient(create_app(store, auth), client=("127.0.0.1", 1)) as api:
        response = api.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "repository_id": str(repository),
                "commit_sha": "a" * 40,
                "incident_text": "duplicate transaction password=synthetic-value",
                "idempotency_key": str(uuid4()),
            },
        )
    assert response.status_code == 202
    run_id = UUID(response.json()["run_id"])
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if outcome == "unavailable":
            return httpx.Response(403, text="sensitive provider detail")
        if "/commits/" in request.url.path:
            payload = {"sha": "a" * 40, "tree": {"sha": root}}
        elif request.url.path.endswith("/trees/" + root):
            payload = {
                "sha": root,
                "truncated": False,
                "tree": [
                    {"path": "src", "type": "tree", "mode": "040000", "sha": subtree}
                ],
            }
        elif request.url.path.endswith("/trees/" + subtree):
            payload = {
                "sha": subtree,
                "truncated": False,
                "tree": [
                    {"path": "a.py", "type": "blob", "mode": "100644", "sha": blob}
                ],
            }
        else:
            body = b"tampered" if outcome == "tampered" else content
            payload = {
                "sha": blob,
                "size": len(body),
                "encoding": "base64",
                "content": base64.b64encode(body).decode(),
            }
        return httpx.Response(200, json=payload)

    restarted = Store(store.dsn)
    cipher = (
        SnapshotCipher(SecretBytes(os.urandom(32)), "ephemeral-test-key")
        if retain_snapshot
        else None
    )
    loader = bind_incident(
        restarted,
        github_inputs(
            read_token,
            ("src/a.py",),
            transport=httpx.MockTransport(handle),
            snapshot_cipher=cipher,
        ),
    )
    result = tick(restarted, uuid4(), loader)
    state = restarted.get(run_id, owner)
    if outcome == "success":
        assert result.stage == Stage.TRIAGING
        context = restarted.context(run_id, owner, state.context_refs[0])
        assert context.evidence_origin == "TEST_TRANSPORT"
        assert state.source_manifest_ref is not None
        manifest = restarted.source_manifest(run_id, owner, state.source_manifest_ref)
        assert manifest.evidence_origin == "TEST_TRANSPORT"
        assert manifest.root_tree == root and manifest.files[0].blob_id == blob
        assert manifest.files[0].sha256 == hashlib.sha256(content).hexdigest()
        if cipher is not None:
            assert state.source_snapshot_ref is not None
            sealed = restarted.sealed_snapshot(run_id, owner, state.source_snapshot_ref)
            assert read_token.get_secret_value() not in sealed.model_dump_json()
            restored = cipher.open(sealed, manifest, grant, owner, run_id)
            assert restored.read_file("src/a.py", 65536) == content
            with pytest.raises(StoreError, match="NOT_FOUND"):
                restarted.sealed_snapshot(run_id, uuid4(), state.source_snapshot_ref)
        else:
            assert state.source_snapshot_ref is None
        assert "content" not in manifest.model_dump()
        assert read_token.get_secret_value() not in manifest.model_dump_json()
        with pytest.raises(StoreError, match="NOT_FOUND"):
            restarted.source_manifest(run_id, uuid4(), state.source_manifest_ref)
        with TestClient(create_app(restarted, auth), client=("127.0.0.1", 1)) as api:
            proof = api.get(
                f"/api/v1/runs/{run_id}/source-manifests/{state.source_manifest_ref}",
                headers=headers,
            )
            assert proof.status_code == 200
            assert proof.json()["root_tree"] == root
        assert context.commit_identity == "GITHUB_HTTPS_METADATA"
        assert "duplicate transaction" in context.sanitized_errors
        assert read_token.get_secret_value() not in context.model_dump_json()
        assert "synthetic-value" not in context.model_dump_json()
    else:
        expected = (
            Stage.INCONCLUSIVE if outcome == "unavailable" else Stage.POLICY_BLOCKED
        )
        assert result.stage == expected
        assert state.failure_reason == (
            "SOURCE_UNAVAILABLE" if outcome == "unavailable" else "SOURCE_REJECTED"
        )
        assert not state.context_refs
    observed = len(calls)
    assert tick(restarted, uuid4(), loader).status == "IDLE"
    assert len(calls) == observed
    with TestClient(create_app(restarted, auth), client=("127.0.0.1", 1)) as api:
        events = api.get(f"/api/v1/runs/{run_id}/events", headers=headers).text
        assert read_token.get_secret_value() not in events
        assert "sensitive provider" not in events


def source_manifest_fixture(store: Store):
    from datetime import UTC, datetime

    from packages.contracts.context import ContextBundle
    from packages.contracts.source import SourceManifest

    owner, state, lease, snapshot = collection_fixture(store)
    for version, target in enumerate(
        (Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING)
    ):
        store.advance(lease, target, expected_version=version)
    bundle = ContextBundle(
        source_commit=state.source_commit,
        sanitized_errors="fixture",
        commit_identity="GITHUB_HTTPS_METADATA",
        evidence_origin="TEST_TRANSPORT",
    )
    manifest = SourceManifest(
        repository_id=state.repository_id,
        source_commit=state.source_commit,
        root_tree="b" * 40,
        evidence_origin="TEST_TRANSPORT",
        observed_at=datetime.now(UTC),
        trace_id=uuid4(),
        files=(),
    )
    return owner, state, lease, bundle, manifest


def test_source_manifest_transaction_rollback(store: Store) -> None:
    owner, state, lease, bundle, manifest = source_manifest_fixture(store)
    before = store.events(state.run_id, owner)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute("""ALTER TABLE context_artifacts
            ADD CONSTRAINT reject_manifest_context
            CHECK (false) NOT VALID""")
    try:
        with pytest.raises(StoreError, match="DB_FAILURE"):
            store.store_context(
                lease, bundle, expected_version=3, source_manifest=manifest
            )
        restored = store.get(state.run_id, owner)
        assert restored.current_stage == Stage.COLLECTING and restored.version == 3
        assert restored.source_manifest_ref is None and not restored.context_refs
        assert store.events(state.run_id, owner) == before
        with psycopg.connect(store.dsn) as conn:
            assert conn.execute(
                "SELECT count(*) FROM source_manifests WHERE run_id=%s", (state.run_id,)
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute(
                "ALTER TABLE context_artifacts DROP CONSTRAINT reject_manifest_context"
            )


def test_source_manifest_retention_tamper_and_runtime_permissions(store: Store) -> None:
    owner, state, lease, bundle, manifest = source_manifest_fixture(store)
    saved = store.store_context(
        lease, bundle, expected_version=3, source_manifest=manifest
    )
    artifact = saved.source_manifest_ref
    assert artifact is not None
    assert Store(store.dsn).source_manifest(state.run_id, owner, artifact) == manifest
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                "DELETE FROM source_manifests WHERE run_id=%s", (state.run_id,)
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                "UPDATE source_manifests SET payload=%s WHERE run_id=%s",
                (b"rewrite", state.run_id),
            )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """UPDATE source_manifests SET expires_at=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.source_manifest(state.run_id, owner, artifact)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """UPDATE source_manifests SET expires_at=
            clock_timestamp()+interval '1 day',payload=%s WHERE run_id=%s""",
            (b"tampered", state.run_id),
        )
    with pytest.raises(StoreError, match="SOURCE_MANIFEST_INTEGRITY"):
        store.source_manifest(state.run_id, owner, artifact)


def test_source_manifest_scope_mismatch_cannot_commit(store: Store) -> None:
    owner, state, lease, bundle, manifest = source_manifest_fixture(store)
    with pytest.raises(StoreError, match="SOURCE_MANIFEST_SCOPE"):
        store.store_context(
            lease,
            bundle,
            expected_version=3,
            source_manifest=manifest.model_copy(update={"repository_id": uuid4()}),
        )
    assert store.get(state.run_id, owner).source_manifest_ref is None
    with psycopg.connect(store.dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM source_manifests WHERE run_id=%s", (state.run_id,)
        ).fetchone() == (0,)


def test_bounded_worker_loop_processes_real_job_then_observes_idle(
    store: Store,
) -> None:
    from threading import Event

    from services.orchestrator.collection import CollectionInputs
    from services.orchestrator.worker import serve

    owner, state, lease, snapshot = collection_fixture(store)
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE job_leases SET status='queued',token=NULL WHERE run_id=%s",
            (state.run_id,),
        )

    def loader(run: RunState, grant: RepositoryGrant) -> CollectionInputs:
        return CollectionInputs(snapshot, ("src/a.py",))

    result = serve(Store(store.dsn), uuid4(), loader, Event(), max_ticks=2)
    assert result.processed == 1 and result.idle == 1 and result.ticks == 2
    assert store.get(state.run_id, owner).current_stage == Stage.TRIAGING
    assert not result.stop_requested


def sealed_fixture(store: Store):
    from types import MappingProxyType

    from pydantic import SecretBytes

    from services.repository.packaging import SnapshotCipher
    from services.repository.snapshot import Snapshot

    owner, state, lease, bundle, manifest = source_manifest_fixture(store)
    grant = store.repository(state.repository_id, owner)
    cipher = SnapshotCipher(SecretBytes(os.urandom(32)), "ephemeral-test-key")
    snapshot = Snapshot(
        state.source_commit,
        (),
        MappingProxyType({}),
        "GITHUB_HTTPS_METADATA",
        "TEST_TRANSPORT",
    )
    sealed = cipher.seal(snapshot, manifest, grant, owner, state.run_id)
    return owner, state, lease, bundle, manifest, sealed


def test_encrypted_snapshot_failure_rolls_back_every_artifact(store: Store) -> None:
    owner, state, lease, bundle, manifest, sealed = sealed_fixture(store)
    before = store.events(state.run_id, owner)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute("""ALTER TABLE run_events ADD CONSTRAINT reject_snapshot_event
            CHECK (event_type <> 'STAGE_CHANGED') NOT VALID""")
    try:
        with pytest.raises(StoreError, match="DB_FAILURE"):
            store.store_context(
                lease,
                bundle,
                expected_version=3,
                source_manifest=manifest,
                sealed_snapshot=sealed,
            )
        restored = store.get(state.run_id, owner)
        assert (
            restored.source_snapshot_ref is None
            and restored.source_manifest_ref is None
        )
        assert not restored.context_refs and restored.version == 3
        assert store.events(state.run_id, owner) == before
        with psycopg.connect(store.dsn) as conn:
            assert conn.execute(
                "SELECT count(*) FROM sealed_snapshots WHERE run_id=%s", (state.run_id,)
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute("ALTER TABLE run_events DROP CONSTRAINT reject_snapshot_event")


def test_encrypted_snapshot_retention_tamper_and_runtime_permissions(
    store: Store,
) -> None:
    owner, state, lease, bundle, manifest, sealed = sealed_fixture(store)
    saved = store.store_context(
        lease,
        bundle,
        expected_version=3,
        source_manifest=manifest,
        sealed_snapshot=sealed,
    )
    ref = saved.source_snapshot_ref
    assert ref is not None
    assert Store(store.dsn).sealed_snapshot(state.run_id, owner, ref) == sealed
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                "DELETE FROM sealed_snapshots WHERE run_id=%s", (state.run_id,)
            )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """UPDATE sealed_snapshots SET expires_at=
            clock_timestamp()-interval '1 second' WHERE run_id=%s""",
            (state.run_id,),
        )
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.sealed_snapshot(state.run_id, owner, ref)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            """UPDATE sealed_snapshots SET expires_at=
            clock_timestamp()+interval '1 day',payload=%s WHERE run_id=%s""",
            (b"tampered", state.run_id),
        )
    with pytest.raises(StoreError, match="SNAPSHOT_INTEGRITY"):
        store.sealed_snapshot(state.run_id, owner, ref)


def test_migration_downgrade_and_reapply(store: Store) -> None:
    # Last scenario in this module; disposable database only.
    command.downgrade(Config("alembic.ini"), "base")
    command.upgrade(Config("alembic.ini"), "head")
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone() == (0,)
