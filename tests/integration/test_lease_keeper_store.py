"""Lease renewal against real PostgreSQL fences; synthetic broker, no providers."""

import time
from contextlib import nullcontext
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from packages.contracts.run import Stage
from services.orchestrator import source_broker
from services.orchestrator.lease_keeper import LeaseKeeper
from services.orchestrator.source_broker import BrokerClient, BrokerError, dispatch_once
from services.persistence.collection_journal import CollectionJournal, DispatchState
from services.persistence.store import StoreError
from tests.integration import test_postgres
from tests.integration.test_collection_reconciliation import (
    OPERATOR,
    REASON,
    allow,
    claimed,
    requeue_job,
)

store = test_postgres.store


def shorten(store, run_id, seconds):
    with store._connection() as db:
        db.execute(
            "UPDATE job_leases SET leased_until=clock_timestamp()"
            "+make_interval(secs=>%s) WHERE run_id=%s",
            (seconds, run_id),
        )


def slow_client(c, store, run_id, *, hold):
    """Broker transport that makes the lease nearly expire, then stalls."""
    internal = TestClient(
        c.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
    )

    def handle(req):
        if req.method == "POST":
            shorten(store, run_id, 1.0)  # the real lease would end during the stall
            time.sleep(hold)
        response = internal.request(
            req.method, str(req.url), headers=dict(req.headers), content=req.content
        )
        return httpx.Response(response.status_code, content=response.content)

    return BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(handle),
    )


def test_keeper_holds_a_real_lease_past_its_original_expiry(store):
    _c, session, lease, _payload, _fetched = claimed(store)
    shorten(store, lease.run_id, 1.0)
    with LeaseKeeper(store, lease, interval_seconds=0.2) as keeper:
        time.sleep(2.2)
        assert store.claimed_run(lease)[0] == session.user_id
    assert keeper.renewals >= 5 and not keeper.lost


def test_without_a_keeper_the_same_lease_expires(store):
    _c, _session, lease, _payload, _fetched = claimed(store)
    shorten(store, lease.run_id, 1.0)
    time.sleep(1.5)
    with pytest.raises(StoreError, match="LEASE_LOST"):
        store.claimed_run(lease)


def test_slow_collection_completes_because_the_lease_is_renewed(store):
    c, session, lease, _payload, fetched = claimed(store)
    run_id = lease.run_id
    requeue_job(store, run_id)
    with slow_client(c, store, run_id, hold=2.5) as client:
        result = dispatch_once(
            store, uuid4(), client, ("README.md",), heartbeat_seconds=0.3
        )
    assert result.stage == Stage.TRIAGING and len(fetched) == 1
    assert CollectionJournal(store).observe(run_id).state == DispatchState.COMPLETED
    assert store.get(run_id, session.user_id).source_manifest_ref


def test_control_without_renewal_the_slow_request_loses_its_lease(store, monkeypatch):
    """Reproduces the defect: a stall past the lease leaves the run unreconciled."""
    monkeypatch.setattr(source_broker, "LeaseKeeper", lambda *_a, **_k: nullcontext())
    c, session, lease, _payload, _fetched = claimed(store)
    run_id = lease.run_id
    requeue_job(store, run_id)
    with slow_client(c, store, run_id, hold=2.5) as client:
        with pytest.raises(BrokerError):
            dispatch_once(store, uuid4(), client, ("README.md",))
    state = store.get(run_id, session.user_id)
    assert state.current_stage != Stage.TRIAGING and not state.context_refs
    # The broker refuses the stale claim (definite) or the reply is lost (uncertain);
    # either way nothing was collected and the run needs attention.
    assert CollectionJournal(store).observe(run_id).state in {
        DispatchState.REJECTED,
        DispatchState.SENT_UNCERTAIN,
    }


def test_a_live_keeper_blocks_reconciliation_and_stops_after_requeue(store):
    _c, session, lease, _payload, _fetched = claimed(store)
    journal = CollectionJournal(store)
    shorten(store, lease.run_id, 1.0)
    with LeaseKeeper(store, lease, interval_seconds=0.2) as keeper:
        time.sleep(1.4)  # past the original expiry; the claim is still live
        with pytest.raises(StoreError, match="LEASE_ACTIVE"):
            journal.reconcile(
                lease.run_id,
                session.user_id,
                "FAIL",
                operator=OPERATOR,
                reason=REASON,
                authorize=allow,
            )
        requeue_job(store, lease.run_id)  # claim is taken away underneath it
        deadline = time.monotonic() + 2
        while not keeper.lost and time.monotonic() < deadline:
            time.sleep(0.05)
        assert keeper.lost  # it did not revive the reset job
    with store._connection() as db:
        row = db.execute(
            "SELECT status,token FROM job_leases WHERE run_id=%s", (lease.run_id,)
        ).fetchone()
    assert row["status"] == "queued" and row["token"] is None
