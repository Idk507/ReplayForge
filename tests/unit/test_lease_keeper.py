"""Lease keeper: bounded renewal of one live claim; never revives or runs forever."""

import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from services.orchestrator.lease_keeper import LeaseKeeper
from services.persistence.store import Lease, StoreError


def lease():
    return Lease(
        run_id=uuid4(), worker_id=uuid4(), token=uuid4(), leased_until=datetime.now(UTC)
    )


class FakeStore:
    def __init__(self, script=()):
        self.calls = 0
        self.script = list(script)
        self.times = []

    def heartbeat(self, held):
        self.calls += 1
        self.times.append(time.monotonic())
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise StoreError(step)
        return held


def wait_for(predicate, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_renews_repeatedly_while_the_call_runs_and_stops_on_exit():
    store = FakeStore()
    with LeaseKeeper(store, lease(), interval_seconds=0.02) as keeper:
        assert wait_for(lambda: store.calls >= 3)
    settled = store.calls
    time.sleep(0.1)
    assert store.calls == settled  # nothing is renewed after the call returned
    assert keeper.renewals >= 3 and not keeper.lost and not keeper.degraded


def test_a_refused_renewal_marks_the_claim_lost_and_stops():
    store = FakeStore(["LEASE_LOST"])
    with LeaseKeeper(store, lease(), interval_seconds=0.02) as keeper:
        assert wait_for(lambda: keeper.lost)
        time.sleep(0.1)
    assert store.calls == 1 and keeper.renewals == 0


def test_transient_database_faults_are_retried_then_give_up_after_three():
    store = FakeStore(["DB_FAILURE_08006", None])
    with LeaseKeeper(store, lease(), interval_seconds=0.02) as keeper:
        assert wait_for(lambda: keeper.renewals >= 1)  # recovered after one fault
    assert not keeper.degraded
    store = FakeStore(["DB_FAILURE_08006"] * 3)
    with LeaseKeeper(store, lease(), interval_seconds=0.02) as keeper:
        assert wait_for(lambda: keeper.degraded)
    assert store.calls == 3 and not keeper.lost


def test_total_renewal_time_is_bounded_so_a_hung_call_loses_its_lease():
    ticks = iter([0.0, 0.5, 1.0, 9999.0] + [9999.0] * 50)
    store = FakeStore()
    with LeaseKeeper(
        store, lease(), interval_seconds=0.02, max_seconds=2, clock=lambda: next(ticks)
    ):
        assert wait_for(lambda: store.calls >= 2)
        time.sleep(0.2)
    assert store.calls == 2  # the third tick exceeded the bound and renewal stopped


def test_an_exception_in_the_guarded_call_still_stops_the_thread():
    store = FakeStore()
    with pytest.raises(RuntimeError):
        with LeaseKeeper(store, lease(), interval_seconds=0.02):
            raise RuntimeError("call failed")
    settled = store.calls
    time.sleep(0.1)
    assert store.calls == settled
    assert not any(t.name == "lease-keeper" for t in threading.enumerate())


@pytest.mark.parametrize(("interval", "maximum"), [(0, 10), (21, 10), (5, 0), (5, 601)])
def test_unsafe_configuration_is_rejected(interval, maximum):
    with pytest.raises(ValueError, match="LEASE_KEEPER_CONFIG"):
        LeaseKeeper(
            FakeStore(), lease(), interval_seconds=interval, max_seconds=maximum
        )
