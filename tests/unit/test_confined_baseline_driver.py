"""Bounded scheduling tests; no provider calls are made."""

import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.contracts.run import Stage
from services.orchestrator import confined_baseline_driver as driver
from services.orchestrator.confined_baseline import ConfinedBaselineResult
from services.persistence.store import StoreError


class FakeStore:
    def __init__(self) -> None:
        self.claims = 0
        self.beats = 0
        self.beat = threading.Event()

    def claim(self, *args: object, **kwargs: object) -> object:
        self.claims += 1
        return SimpleNamespace(run_id=kwargs["run_id"])

    def claimed_run(self, lease: object) -> tuple[object, object]:
        return uuid4(), object()

    def heartbeat(self, lease: object) -> object:
        self.beats += 1
        if self.beats >= 2:
            self.beat.set()
        return lease


def test_pending_polling_retains_one_lease_and_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore()
    provider = SimpleNamespace(run_id=uuid4(), origin="TEST_TRANSPORT")
    operation, execution = uuid4(), uuid4()

    def advance(*args: object, **kwargs: object) -> ConfinedBaselineResult:
        assert store.beat.wait(2)
        return ConfinedBaselineResult(
            status="PENDING",
            run_id=provider.run_id,
            execution_id=execution,
            operation_id=operation,
            stage=Stage.REPRODUCING,
        )

    monkeypatch.setattr(driver, "advance_confined_baseline", advance)
    result = driver.run_confined_baseline(
        store,
        uuid4(),
        provider,
        threading.Event(),
        max_polls=2,
        poll_seconds=0.01,
        heartbeat_seconds=0.01,
    )
    assert result.status == "PENDING" and result.polls == 2
    assert result.result is not None and result.result.operation_id == operation
    assert store.claims == 1 and store.beats >= 2


def test_unknown_submission_never_polls_again(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore()
    provider = SimpleNamespace(run_id=uuid4(), origin="TEST_TRANSPORT")
    calls = []

    def advance(*args: object, **kwargs: object) -> ConfinedBaselineResult:
        calls.append(1)
        return ConfinedBaselineResult(
            status="SUBMISSION_UNKNOWN",
            run_id=provider.run_id,
            execution_id=uuid4(),
            operation_id=None,
            stage=Stage.REPRODUCING,
        )

    monkeypatch.setattr(driver, "advance_confined_baseline", advance)
    result = driver.run_confined_baseline(store, uuid4(), provider, threading.Event())
    assert result.status == "SUBMISSION_UNKNOWN" and len(calls) == 1


def test_stopped_and_unauthorized_do_not_claim() -> None:
    store = FakeStore()
    stop = threading.Event()
    stop.set()
    provider = SimpleNamespace(run_id=uuid4(), origin="TEST_TRANSPORT")
    assert (
        driver.run_confined_baseline(store, uuid4(), provider, stop).status == "STOPPED"
    )
    provider.origin = "REMOTE_SERVICE"
    with pytest.raises(StoreError, match="BASELINE_LIVE_NOT_AUTHORIZED"):
        driver.run_confined_baseline(store, uuid4(), provider, threading.Event())
    assert store.claims == 0


def test_live_driver_requires_current_repository_authorization_before_claim() -> None:
    store = FakeStore()
    provider = SimpleNamespace(run_id=uuid4(), origin="REMOTE_SERVICE")
    with pytest.raises(StoreError, match="BASELINE_AUTHORIZATION_REQUIRED"):
        driver.run_confined_baseline(
            store, uuid4(), provider, threading.Event(), allow_live=True
        )
    assert store.claims == 0


def test_live_direct_controller_requires_authorization_before_claim_lookup() -> None:
    from services.orchestrator.confined_baseline import advance_confined_baseline

    store = FakeStore()
    provider = SimpleNamespace(run_id=uuid4(), origin="REMOTE_SERVICE")
    with pytest.raises(StoreError, match="BASELINE_AUTHORIZATION_REQUIRED"):
        advance_confined_baseline(store, object(), uuid4(), provider, allow_live=True)
    assert store.claims == 0


def test_heartbeat_failure_stops_after_inflight_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore()
    provider = SimpleNamespace(run_id=uuid4(), origin="TEST_TRANSPORT")
    calls: list[int] = []
    lost = threading.Event()

    def heartbeat(lease: object) -> object:
        store.beats += 1
        if store.beats > 1:
            lost.set()
            raise StoreError("LEASE_LOST")
        return lease

    def advance(*args: object, **kwargs: object) -> ConfinedBaselineResult:
        calls.append(1)
        assert lost.wait(2)
        time.sleep(0.03)
        return ConfinedBaselineResult(
            status="PENDING",
            run_id=provider.run_id,
            execution_id=uuid4(),
            operation_id=uuid4(),
            stage=Stage.REPRODUCING,
        )

    monkeypatch.setattr(store, "heartbeat", heartbeat)
    monkeypatch.setattr(driver, "advance_confined_baseline", advance)
    with pytest.raises(StoreError, match="BASELINE_HEARTBEAT_LOST"):
        driver.run_confined_baseline(
            store, uuid4(), provider, threading.Event(), heartbeat_seconds=0.01
        )
    assert len(calls) == 1 and store.claims == 1


def test_stop_during_pending_wait_keeps_known_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore()
    provider = SimpleNamespace(run_id=uuid4(), origin="TEST_TRANSPORT")
    operation, calls = uuid4(), []

    class StopOnWait:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            return True

    def advance(*args: object, **kwargs: object) -> ConfinedBaselineResult:
        calls.append(1)
        return ConfinedBaselineResult(
            status="PENDING",
            run_id=provider.run_id,
            execution_id=uuid4(),
            operation_id=operation,
            stage=Stage.REPRODUCING,
        )

    monkeypatch.setattr(driver, "advance_confined_baseline", advance)
    result = driver.run_confined_baseline(store, uuid4(), provider, StopOnWait())
    assert result.status == "STOPPED" and result.polls == 1
    assert result.result is not None and result.result.operation_id == operation
    assert len(calls) == 1 and store.claims == 1
    assert "junit" not in str(result.model_dump()).lower()


def test_elapsed_budget_prevents_another_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore()
    provider = SimpleNamespace(run_id=uuid4(), origin="TEST_TRANSPORT")
    operation, calls = uuid4(), []
    now = [0.0]

    class ExpiringWait:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            now[0] += timeout
            return False

    def advance(*args: object, **kwargs: object) -> ConfinedBaselineResult:
        calls.append(1)
        return ConfinedBaselineResult(
            status="PENDING",
            run_id=provider.run_id,
            execution_id=uuid4(),
            operation_id=operation,
            stage=Stage.REPRODUCING,
        )

    monkeypatch.setattr(driver, "advance_confined_baseline", advance)
    monkeypatch.setattr(driver.time, "monotonic", lambda: now[0])
    result = driver.run_confined_baseline(
        store,
        uuid4(),
        provider,
        ExpiringWait(),
        max_seconds=1,
        poll_seconds=2,
    )
    assert result.status == "PENDING" and result.polls == 1
    assert result.result is not None and result.result.operation_id == operation
    assert len(calls) == 1
