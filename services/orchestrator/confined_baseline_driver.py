"""Bounded lease-owning driver for the durable confined baseline controller."""

import math
import threading
import time
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from packages.contracts.run import Stage
from services.orchestrator.confined_baseline import (
    ConfinedBaselineResult,
    advance_confined_baseline,
)
from services.orchestrator.worker import StopSignal
from services.persistence.store import Lease, Store, StoreError
from services.runner.confined_baseline_provider import ConfinedBaselineProvider


class BaselineDriverResult(BaseModel):
    """Control-plane outcome; stopping does not prove remote termination."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal[
        "IDLE",
        "STOPPED",
        "PENDING",
        "SUBMISSION_UNKNOWN",
        "TEST_EVIDENCE_RETAINED",
        "DELIVERED",
    ]
    polls: int
    result: ConfinedBaselineResult | None = None


def run_confined_baseline(
    store: Store,
    worker_id: UUID,
    provider: ConfinedBaselineProvider,
    stop: StopSignal,
    *,
    allow_live: bool = False,
    queue_patch: bool = False,
    max_polls: int = 30,
    poll_seconds: float = 2.0,
    heartbeat_seconds: float = 5.0,
    max_seconds: float = 90.0,
    authorize: Callable[[Lease, int], None] | None = None,
) -> BaselineDriverResult:
    """Claim once and poll under one continuously renewed, fenced lease.

    A timeout stops polling, not the provider operation. An uncertain submission
    is returned immediately and must be reconciled through its durable journal.
    """
    values = (poll_seconds, heartbeat_seconds, max_seconds)
    if (
        type(max_polls) is not int
        or not 1 <= max_polls <= 100
        or any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v <= 0
            for v in values
        )
        or heartbeat_seconds > 10
        or max_seconds > 900
        or type(queue_patch) is not bool
        or type(allow_live) is not bool
    ):
        raise StoreError("BASELINE_DRIVER_INVALID")
    if provider.origin == "REMOTE_SERVICE" and not allow_live:
        raise StoreError("BASELINE_LIVE_NOT_AUTHORIZED")
    if provider.origin == "REMOTE_SERVICE" and not callable(authorize):
        raise StoreError("BASELINE_AUTHORIZATION_REQUIRED")
    if stop.is_set():
        return BaselineDriverResult(status="STOPPED", polls=0)
    lease = store.claim(worker_id, stages=(Stage.REPRODUCING,), run_id=provider.run_id)
    if lease is None:
        return BaselineDriverResult(status="IDLE", polls=0)
    owner, _ = store.claimed_run(lease)
    store.heartbeat(lease)
    finished = threading.Event()
    failed = threading.Event()

    def heartbeat() -> None:
        while not finished.wait(heartbeat_seconds):
            try:
                store.heartbeat(lease)
            except Exception:
                failed.set()
                return

    thread = threading.Thread(target=heartbeat, name="baseline-heartbeat", daemon=True)
    thread.start()
    start = time.monotonic()
    polls = 0
    last: ConfinedBaselineResult | None = None

    def check() -> None:
        if failed.is_set():
            raise StoreError("BASELINE_HEARTBEAT_LOST")

    def guarded_authorize(current: Lease, version: int) -> None:
        check()
        if stop.is_set():
            raise StoreError("BASELINE_DRIVER_STOPPED")
        if authorize is not None:
            authorize(current, version)

    try:
        while polls < max_polls:
            check()
            if stop.is_set():
                return BaselineDriverResult(status="STOPPED", polls=polls, result=last)
            if last is not None and time.monotonic() - start >= max_seconds:
                return BaselineDriverResult(status="PENDING", polls=polls, result=last)
            last = advance_confined_baseline(
                store,
                lease,
                owner,
                provider,
                allow_live=allow_live,
                queue_patch=queue_patch,
                authorize=guarded_authorize,
            )
            polls += 1
            # Successful delivery may atomically release the lease for PATCHING.
            if last.status == "DELIVERED":
                return BaselineDriverResult(
                    status=last.status, polls=polls, result=last
                )
            check()
            if last.status != "PENDING":
                return BaselineDriverResult(
                    status=last.status, polls=polls, result=last
                )
            remaining = max_seconds - (time.monotonic() - start)
            if polls >= max_polls or remaining <= 0:
                return BaselineDriverResult(status="PENDING", polls=polls, result=last)
            if stop.wait(min(poll_seconds, remaining)):
                return BaselineDriverResult(status="STOPPED", polls=polls, result=last)
        raise StoreError("BASELINE_DRIVER_INVALID")
    finally:
        finished.set()
        thread.join(timeout=12)
        if thread.is_alive():
            raise StoreError("BASELINE_HEARTBEAT_STOP_TIMEOUT")
