"""One bounded collection dispatch; caller controls scheduling and input adapter."""

import math
from dataclasses import replace
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from services.orchestrator.collection import (
    CollectionError,
    CollectionInputs,
    InputLoader,
    run_collection,
)
from services.persistence.store import Store, StoreError


def bind_incident(store: Store, source_loader: InputLoader) -> InputLoader:
    """Bind persisted incident to collection after graph scope admission.

    No fallback to caller text: missing/expired reports stop before source I/O.
    The provider's stack traces remain untrusted and are redacted by collection.
    """

    def load(run: RunState, grant: RepositoryGrant) -> CollectionInputs:
        try:
            report = store.incident(run.run_id, grant.owner_id)
        except StoreError as error:
            if str(error) == "NOT_FOUND":
                raise CollectionError("INCIDENT_UNAVAILABLE") from None
            raise
        inputs = source_loader(run, grant)
        return replace(inputs, errors=report.text + "\n" + inputs.errors)

    return load


class WorkerResult(BaseModel):
    """Safe outcome; no source bytes, incident bodies or credential values."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["IDLE", "PROCESSED"]
    run_id: UUID | None = None
    stage: Stage | None = None


def tick(store: Store, worker_id: UUID, input_loader: InputLoader) -> WorkerResult:
    """Claim at most one job and dispatch through the fenced graph.

    No automatic retries or infinite loops. Provider exceptions propagate and
    leave the claim for bounded expiry/recovery. A production loader must enforce
    its own byte/time/egress policy; synthetic adapters never prove live fetch.
    """
    lease = store.claim(worker_id)
    if lease is None:
        return WorkerResult(status="IDLE")
    owner, _ = store.claimed_run(lease)
    result = run_collection(store, owner, lease, input_loader=input_loader)
    return WorkerResult(
        status="PROCESSED", run_id=result.run_id, stage=result.current_stage
    )


class StopSignal(Protocol):
    """threading.Event-compatible cooperative stop, injectable without sleeping."""

    def is_set(self) -> bool: ...
    def wait(self, timeout: float) -> bool: ...


class WorkerLoopResult(BaseModel):
    """Safe aggregate metadata; stopping is not cancellation of individual runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ticks: int = Field(ge=0)
    processed: int = Field(ge=0)
    idle: int = Field(ge=0)
    stop_requested: bool


def serve(
    store: Store,
    worker_id: UUID,
    input_loader: InputLoader,
    stop: StopSignal,
    *,
    max_ticks: int = 100,
    idle_seconds: float = 1,
) -> WorkerLoopResult:
    """Bounded polling with cooperative shutdown; never silently retry failures.

    No daemon is started by import/construction. The operator must configure a
    trusted loader and explicitly call this function. In-flight dispatch finishes
    or times out before shutdown; untrusted source is never executed here.
    """
    if (
        type(max_ticks) is not int
        or not 1 <= max_ticks <= 1000
        or type(idle_seconds) not in (int, float)
        or not math.isfinite(idle_seconds)
        or not 0 < idle_seconds <= 60
    ):
        raise ValueError("POLLING_LIMIT")
    ticks = processed = idle = 0
    while ticks < max_ticks and not stop.is_set():
        result = tick(store, worker_id, input_loader)
        ticks += 1
        if result.status == "PROCESSED":
            processed += 1
        else:
            idle += 1
            if ticks < max_ticks and stop.wait(idle_seconds):
                break
    return WorkerLoopResult(
        ticks=ticks, processed=processed, idle=idle, stop_requested=stop.is_set()
    )
