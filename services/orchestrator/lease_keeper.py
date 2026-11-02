"""Renew a worker's fenced lease while one long blocking call runs.

The lease lasts 30 s but a collection request, model call or measured action can
legitimately take longer. A background thread extends only the caller's own live
claim every few seconds, for a bounded total time, and stops the moment the claim
is lost (operator requeue, expiry) or the call returns. It never revives an expired
claim (``Store.heartbeat`` refuses) and never extends past ``max_seconds``, so a
hung call still fails closed. It performs no external action itself.
"""

import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self

from packages.observability.logs import log_event
from services.persistence.store import Lease, StoreError


class Heartbeating(Protocol):
    def heartbeat(self, lease: Lease) -> Lease: ...


class LeaseKeeper:
    """Context manager; ``lost`` is True once the store refused a renewal."""

    def __init__(
        self,
        store: Heartbeating,
        lease: Lease,
        *,
        interval_seconds: float = 10.0,
        max_seconds: float = 180.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < interval_seconds <= 20 or not 0 < max_seconds <= 600:
            raise ValueError("LEASE_KEEPER_CONFIG")
        self._store, self._lease = store, lease
        self._interval, self._max, self._clock = interval_seconds, max_seconds, clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.renewals = 0
        self.lost = False
        self.degraded = False

    def _run(self) -> None:
        started = self._clock()
        failures = 0
        while not self._stop.wait(self._interval):
            if self._clock() - started > self._max:
                return  # bounded: a hung call is allowed to lose its lease
            try:
                self._lease = self._store.heartbeat(self._lease)
                self.renewals += 1
                failures = 0
            except StoreError as error:
                if str(error) == "LEASE_LOST":
                    self.lost = True
                    log_event(
                        "lease.lost",
                        level=30,
                        run_id=self._lease.run_id,
                        worker_id=self._lease.worker_id,
                    )
                    return
                failures += 1  # transient database fault: try again next interval
                if failures >= 3:
                    self.degraded = True
                    log_event(
                        "lease.renewal_degraded",
                        level=30,
                        run_id=self._lease.run_id,
                        worker_id=self._lease.worker_id,
                        error_class=str(error)[:48],
                    )
                    return

    def __enter__(self) -> Self:
        self._thread = threading.Thread(
            target=self._run, name="lease-keeper", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        typ: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
