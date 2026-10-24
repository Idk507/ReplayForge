"""Stage-specific baseline worker over operator-prepared, immutable workspaces."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from packages.contracts.run import RunState, Stage
from services.orchestrator.baseline_dispatch import (
    BaselineDispatchResult,
    dispatch_baseline,
)
from services.orchestrator.replay_dispatch import PreparedTestBackend
from services.orchestrator.reproduction import ReproductionArtifact
from services.persistence.baseline import BaselineAction, BaselineJournal
from services.persistence.reproduction import ReproductionJournal
from services.persistence.store import Store, StoreError
from services.runner.admission import CapabilityAttestation, CapabilityVerifier


@dataclass(frozen=True)
class PreparedBaseline:
    """Trusted deployment inputs, not browser/model JSON or measured facts."""

    action: BaselineAction
    attestation: CapabilityAttestation
    verifier: CapabilityVerifier
    backend: PreparedTestBackend


class PreparedBaselineProvider(Protocol):
    """Resolve a stable existing preparation; never clone/upload/submit here.

    Resolution must be read-only and return the same execution/plan after restart.
    Preparation needs its own durable intent before external I/O and is outside
    this port. Context exit releases client handles, not proof of compute cleanup.
    """

    @property
    def origin(self) -> Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]: ...

    def resolve(
        self, run: RunState, reproduction: ReproductionArtifact
    ) -> AbstractContextManager[PreparedBaseline]: ...


def tick_prepared_baseline(
    store: Store,
    worker_id: UUID,
    provider: PreparedBaselineProvider,
    *,
    allow_live: bool = False,
) -> BaselineDispatchResult | None:
    """Claim REPRODUCING only and submit once through current admission gates."""
    if provider.origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}:
        raise StoreError("BASELINE_BACKEND_INVALID")
    if provider.origin == "REMOTE_SERVICE" and allow_live is not True:
        raise StoreError("BASELINE_LIVE_NOT_AUTHORIZED")
    lease = store.claim(worker_id, stages=(Stage.REPRODUCING,))
    if lease is None:
        return None
    owner, run = store.claimed_run(lease)
    reproduction = ReproductionJournal(store).get(run.run_id, owner)
    with provider.resolve(run, reproduction) as prepared:
        if prepared.backend.origin != provider.origin:
            raise StoreError("BASELINE_BACKEND_INVALID")
        BaselineJournal(store).record_action(
            lease, owner, prepared.action, expected_version=run.version
        )
        return dispatch_baseline(
            store,
            lease,
            owner,
            prepared.action.execution_id,
            prepared.attestation,
            prepared.verifier,
            prepared.backend,
            expected_version=run.version,
            allow_live=allow_live,
        )
