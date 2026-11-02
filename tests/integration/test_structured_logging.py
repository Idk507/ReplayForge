"""Operational events from real worker, lease and reconciliation paths."""

import io
import json
import logging
import time
from uuid import uuid4

import httpx
import pytest

from packages.observability.logs import LOGGER_NAME, configure
from services.orchestrator.lease_keeper import LeaseKeeper
from services.orchestrator.source_broker import BrokerError, dispatch_once
from services.persistence.collection_journal import CollectionJournal
from services.persistence.store import StoreError
from tests.integration import test_postgres
from tests.integration.test_collection_reconciliation import (
    OPERATOR,
    REASON,
    allow,
    broker_client,
    claimed,
    expire,
    requeue_job,
)

store = test_postgres.store


@pytest.fixture
def stream():
    buffer = io.StringIO()
    configure(buffer, logging.DEBUG)
    yield buffer
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, "_rf_json", False):
            logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


def records(buffer):
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line]


def test_successful_collection_logs_ordered_correlated_events_without_secrets(
    store, stream
):
    c, session, lease, payload, _fetched = claimed(store)
    requeue_job(store, lease.run_id)
    client, _posts = broker_client(c)
    with client:
        dispatch_once(store, uuid4(), client, ("README.md",))
    events = records(stream)
    names = [e["event"] for e in events]
    assert names == [
        "collection.intent",
        "collection.sent",
        "collection.dispatch.ok",
        "collection.outcome",
    ]
    assert len({e["operation_id"] for e in events}) == 1
    assert all(e["run_id"] == str(lease.run_id) for e in events)
    assert all(e["source_commit"] == "a" * 40 for e in events)
    assert events[-1]["status"] == "COMPLETED" and events[-1]["stage"] == "TRIAGING"
    assert isinstance(events[2]["duration_ms"], int)
    text = stream.getvalue()
    assert str(lease.token) not in text  # the fencing capability is never logged
    assert "s" * 43 not in text and "authorization" not in text.lower()
    assert "dropped_fields" not in text


def test_unconfirmed_collection_is_logged_as_uncertain(store, stream):
    c, _session, lease, _payload, _fetched = claimed(store)
    requeue_job(store, lease.run_id)
    client, _posts = broker_client(c, fail=httpx.ConnectError("lost"))
    with client:
        with pytest.raises(BrokerError):
            dispatch_once(store, uuid4(), client, ("README.md",))
    events = records(stream)
    assert events[-1]["event"] == "collection.outcome"
    assert events[-1]["status"] == "UNCERTAIN"
    assert any(e["event"] == "collection.dispatch.error" for e in events)
    assert "lost" not in stream.getvalue()  # exception text is never copied


def test_lost_lease_and_reconciliation_decisions_are_logged(store, stream):
    _c, session, lease, _payload, _fetched = claimed(store)
    with LeaseKeeper(store, lease, interval_seconds=0.1) as keeper:
        requeue_job(store, lease.run_id)
        deadline = time.monotonic() + 2
        while not keeper.lost and time.monotonic() < deadline:
            time.sleep(0.05)
    assert any(e["event"] == "lease.lost" for e in records(stream))

    _c2, session2, lease2, _p2, _f2 = claimed(store)
    expire(store, lease2.run_id)
    journal = CollectionJournal(store)
    journal.reconcile(
        lease2.run_id,
        session2.user_id,
        "FAIL",
        operator=OPERATOR,
        reason=REASON,
        authorize=allow,
    )
    with pytest.raises(StoreError):
        journal.reconcile(
            lease2.run_id,
            session2.user_id,
            "REQUEUE",
            operator=OPERATOR,
            reason=REASON,
            authorize=allow,
        )
    names = [e["event"] for e in records(stream)]
    assert "reconcile.decision" in names and "reconcile.blocked" in names
    decision = next(e for e in records(stream) if e["event"] == "reconcile.decision")
    assert decision["action"] == "FAIL" and decision["operator"] == OPERATOR
    assert REASON not in stream.getvalue()  # free-text reasons stay in the audit table
