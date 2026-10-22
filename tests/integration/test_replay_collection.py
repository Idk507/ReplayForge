"""Durable report collection with real PostgreSQL and inert mock provider bytes."""

from uuid import uuid4

import httpx
import pytest

from services.orchestrator.replay_dispatch import dispatch_replay
from services.persistence.replay import ReplayJournal
from services.persistence.store import Store
from tests.contract.test_sandbox_smoke import config
from tests.integration import test_postgres
from tests.integration.test_replay_delivery import prepared
from tests.integration.test_replay_dispatch import FakeBackend
from tests.unit.test_runner_admission import fixture

store = test_postgres.store


def setup_collection(store):
    lease, owner, run, actions, *_ = prepared(store, reserve=False)
    actions = actions.model_copy(
        update={
            "candidate_plan": actions.candidate_plan.model_copy(
                update={"command_id": "PYTEST_FILES_SAVED_JUNIT_V1"}
            ),
            "clean_plan": actions.clean_plan.model_copy(
                update={"command_id": "PYTEST_FILES_SAVED_JUNIT_V1"}
            ),
        }
    )
    run = ReplayJournal(store).reserve(
        lease, owner, actions, expected_version=run.version
    )
    _, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
    backend = FakeBackend(profile)
    dispatched = dispatch_replay(
        store,
        lease,
        owner,
        "CANDIDATE",
        sign(),
        verifier,
        backend,
        expected_version=run.version,
    )
    return lease, owner, run, actions, dispatched


@pytest.mark.parametrize(
    "status,exit_code", [("SUCCESS", 0), ("SUCCESS", 1), ("FAILED", 1)]
)
def test_replay_collection_retains_exact_bytes_and_restart_avoids_http(
    store, status, exit_code
):
    from services.orchestrator.replay_collection import collect_replay

    lease, owner, run, actions, sent = setup_collection(store)
    image = uuid4()
    body = b'<testsuite tests="1"><testcase name="demo"/></testsuite>'
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        if "/operations/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "uuid": str(sent.operation_id),
                    "kind": "instance",
                    "status": status,
                    "image_uuid": str(config().image_uuid),
                    "result_image_uuid": str(image),
                    "metadata": {
                        "result": {
                            "state": {
                                "exit_code": exit_code,
                                "signal": None,
                                "timed_out": False,
                            }
                        }
                    },
                },
            )
        assert str(image) in request.url.path
        assert request.url.params["path"] == "/tmp/replayforge-result.xml"
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    result = collect_replay(
        store,
        lease,
        owner,
        "CANDIDATE",
        config(),
        expected_version=run.version,
        transport=transport,
    )
    assert result.status == "REPORT_COLLECTED"
    assert result.junit_bytes() == body
    assert result.process_state.exit_code == exit_code
    assert result.execution_id == actions.candidate_execution_id
    assert result.evidence_origin == "TEST_TRANSPORT"
    assert not result.can_authorize_verification
    assert "<testsuite" not in repr(result)
    assert (
        collect_replay(
            Store(store.dsn),
            lease,
            owner,
            "CANDIDATE",
            config(),
            expected_version=run.version,
            transport=transport,
        )
        == result
    )
    assert len(calls) == 2
    assert store.get(run.run_id, owner) == run
    assert (
        store.events(run.run_id, owner)[-1].event_type == "REPLAY_OBSERVATION_RETAINED"
    )


@pytest.mark.parametrize(
    "fault", ["owner", "lease", "version", "unknown", "phase", "consent"]
)
def test_replay_collection_rejects_before_http(store, fault):
    import os

    import psycopg
    from psycopg.types.json import Jsonb

    from services.orchestrator.replay_collection import collect_replay
    from services.persistence.store import StoreError

    lease, owner, run, _, _ = setup_collection(store)
    if fault == "lease":
        lease = lease.model_copy(update={"token": uuid4()})
    if fault in {"unknown", "consent"}:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "unknown":
                conn.execute(
                    "DELETE FROM replay_submission_results WHERE run_id=%s",
                    (run.run_id,),
                )
            else:
                grant = store.repository(run.repository_id, owner)
                conn.execute(
                    "UPDATE repository_grants SET grant_json=%s WHERE id=%s",
                    (
                        Jsonb(
                            grant.model_copy(update={"consent": False}).model_dump(
                                mode="json"
                            )
                        ),
                        run.repository_id,
                    ),
                )
    with pytest.raises(StoreError):
        collect_replay(
            store,
            lease,
            uuid4() if fault == "owner" else owner,
            "CLEAN_REPLAY" if fault == "phase" else "CANDIDATE",
            config(),
            expected_version=run.version + (fault == "version"),
            transport=httpx.MockTransport(lambda r: pytest.fail("HTTP before binding")),
        )


def provider(sent, *, status="SUCCESS", image=None, hook=None):
    result_image = uuid4() if image is None else image
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        if "/operations/" in request.url.path:
            if hook:
                hook()
            return httpx.Response(
                200,
                json={
                    "uuid": str(sent.operation_id),
                    "kind": "instance",
                    "status": status,
                    "image_uuid": str(config().image_uuid),
                    "result_image_uuid": str(result_image),
                    "metadata": {
                        "result": {
                            "state": {
                                "exit_code": 0,
                                "signal": None,
                                "timed_out": False,
                            }
                        }
                    },
                },
            )
        return httpx.Response(200, content=b'<testsuite tests="0"/>')

    return httpx.MockTransport(handler), calls


def test_replay_collection_pending_is_not_retained_and_can_observe_terminal_later(
    store,
):
    from services.orchestrator.replay_collection import collect_replay

    lease, owner, run, _, sent = setup_collection(store)
    pending, calls = provider(sent, status="EXECUTING")
    observed = collect_replay(
        store,
        lease,
        owner,
        "CANDIDATE",
        config(),
        expected_version=run.version,
        transport=pending,
    )
    assert observed.status == "PENDING" and observed.junit_bytes() is None
    assert len(calls) == 1
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM replay_observations WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 0
        )
    transport, _ = provider(sent)
    assert (
        collect_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            config(),
            expected_version=run.version,
            transport=transport,
        ).status
        == "REPORT_COLLECTED"
    )


def test_replay_collection_fences_cancellation_during_io(store):
    from services.orchestrator.replay_collection import collect_replay
    from services.persistence.store import StoreError

    lease, owner, run, _, sent = setup_collection(store)
    transport, _ = provider(
        sent, hook=lambda: store.cancel(run.run_id, owner, expected_version=run.version)
    )
    with pytest.raises(StoreError):
        collect_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            config(),
            expected_version=run.version,
            transport=transport,
        )
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM replay_observations WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize("fault", ["tamper", "expiry", "permissions"])
def test_replay_collection_private_records_have_integrity_expiry_and_immutable_role(
    store, fault
):
    import os

    import psycopg

    from services.orchestrator.replay_collection import collect_replay
    from services.persistence.store import StoreError

    lease, owner, run, _, sent = setup_collection(store)
    transport, _ = provider(sent)
    collect_replay(
        store,
        lease,
        owner,
        "CANDIDATE",
        config(),
        expected_version=run.version,
        transport=transport,
    )
    if fault == "permissions":
        for statement in [
            "UPDATE replay_observations SET payload=payload WHERE run_id=%s",
            "DELETE FROM replay_observations WHERE run_id=%s",
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(store.dsn) as conn:
                    conn.execute(statement, (run.run_id,))
    else:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "tamper":
                conn.execute(
                    "UPDATE replay_observations SET payload=%s WHERE run_id=%s",
                    (b"tampered", run.run_id),
                )
            else:
                conn.execute(
                    "UPDATE replay_observations SET "
                    "expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                    (run.run_id,),
                )
        with pytest.raises(StoreError):
            collect_replay(
                store,
                lease,
                owner,
                "CANDIDATE",
                config(),
                expected_version=run.version,
                transport=httpx.MockTransport(
                    lambda r: pytest.fail("reread unusable cache")
                ),
            )


def test_replay_collection_durable_terminal_wins_racing_pending_read(store):
    from services.orchestrator.replay_collection import collect_replay

    lease, owner, run, _, sent = setup_collection(store)
    retained = []

    def complete():
        transport, _ = provider(sent)
        retained.append(
            collect_replay(
                Store(store.dsn),
                lease,
                owner,
                "CANDIDATE",
                config(),
                expected_version=run.version,
                transport=transport,
            )
        )

    pending, _ = provider(sent, status="EXECUTING", hook=complete)
    result = collect_replay(
        store,
        lease,
        owner,
        "CANDIDATE",
        config(),
        expected_version=run.version,
        transport=pending,
    )
    assert result == retained[0]


def test_replay_collection_audit_failure_rolls_back_private_record(store, monkeypatch):
    from services.orchestrator.replay_collection import collect_replay
    from services.persistence.store import StoreError

    lease, owner, run, _, sent = setup_collection(store)
    events = store.events(run.run_id, owner)

    def fail(*args):
        raise StoreError("SYNTHETIC_AUDIT_FAILURE")

    monkeypatch.setattr(Store, "_event", staticmethod(fail))
    transport, _ = provider(sent)
    with pytest.raises(StoreError, match="SYNTHETIC_AUDIT_FAILURE"):
        collect_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            config(),
            expected_version=run.version,
            transport=transport,
        )
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM replay_observations WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 0
        )
    assert store.events(run.run_id, owner) == events


def test_replay_collection_concurrent_terminal_reads_reconcile_one_record_and_event(
    store,
):
    from concurrent.futures import ThreadPoolExecutor

    from services.orchestrator.replay_collection import collect_replay

    lease, owner, run, _, sent = setup_collection(store)
    transport, _ = provider(sent)

    def collect(_):
        return collect_replay(
            Store(store.dsn),
            lease,
            owner,
            "CANDIDATE",
            config(),
            expected_version=run.version,
            transport=transport,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(collect, range(2)))
    assert results[0] == results[1]
    assert (
        sum(
            e.event_type == "REPLAY_OBSERVATION_RETAINED"
            for e in store.events(run.run_id, owner)
        )
        == 1
    )
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM replay_observations WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 1
        )
