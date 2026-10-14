"""Bounded polling tests use no threads, sleeps, network or database."""

from uuid import uuid4

import pytest

from services.orchestrator.worker import WorkerResult, serve
from services.persistence.store import Store, StoreError


class Stop:
    def __init__(self, stopped=False, stop_on_wait=False):
        self.stopped = stopped
        self.stop_on_wait = stop_on_wait
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.waits.append(timeout)
        self.stopped = self.stop_on_wait
        return self.stopped


def unused_loader(*args):
    raise AssertionError("not a source test")


def test_stop_before_poll_never_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.orchestrator.worker.tick",
        lambda *args: pytest.fail("unexpected claim"),
    )
    result = serve(Store("unused"), uuid4(), unused_loader, Stop(stopped=True))
    assert result.ticks == 0 and result.stop_requested


def test_idle_polling_is_bounded_and_waits_between_ticks(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.orchestrator.worker.tick", lambda *args: WorkerResult(status="IDLE")
    )
    stop = Stop()
    result = serve(
        Store("unused"), uuid4(), unused_loader, stop, max_ticks=3, idle_seconds=2
    )
    assert result.ticks == 3 and result.idle == 3 and result.processed == 0
    assert stop.waits == [2, 2]


def test_processed_jobs_do_not_wait_and_still_obey_tick_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.orchestrator.worker.tick",
        lambda *args: WorkerResult(status="PROCESSED"),
    )
    stop = Stop()
    result = serve(Store("unused"), uuid4(), unused_loader, stop, max_ticks=3)
    assert result.processed == 3 and result.idle == 0 and result.ticks == 3
    assert not stop.waits


def test_stop_during_idle_wait_exits_without_another_claim(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.orchestrator.worker.tick", lambda *args: WorkerResult(status="IDLE")
    )
    result = serve(Store("unused"), uuid4(), unused_loader, Stop(stop_on_wait=True))
    assert result.ticks == 1 and result.stop_requested


def test_worker_failure_propagates_without_automatic_retry(monkeypatch) -> None:
    calls = []

    def failed(*args):
        calls.append(1)
        raise StoreError("DB_FAILURE_UNAVAILABLE")

    monkeypatch.setattr("services.orchestrator.worker.tick", failed)
    with pytest.raises(StoreError, match="DB_FAILURE_UNAVAILABLE"):
        serve(Store("unused"), uuid4(), unused_loader, Stop())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "options",
    [
        {"max_ticks": 0},
        {"max_ticks": True},
        {"max_ticks": 1001},
        {"idle_seconds": 0},
        {"idle_seconds": float("nan")},
        {"idle_seconds": 61},
    ],
)
def test_unbounded_polling_configuration_rejected(options) -> None:
    with pytest.raises(ValueError, match="POLLING_LIMIT"):
        serve(Store("unused"), uuid4(), unused_loader, Stop(), **options)
