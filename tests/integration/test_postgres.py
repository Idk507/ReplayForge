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


def test_migration_downgrade_and_reapply(store: Store) -> None:
    # Last scenario in this module; disposable database only.
    command.downgrade(Config("alembic.ini"), "base")
    command.upgrade(Config("alembic.ini"), "head")
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone() == (0,)
