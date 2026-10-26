"""One admitted patch-generation step after an accepted behavioral baseline."""

from collections.abc import Callable
from uuid import UUID

import httpx

from packages.contracts.inference import ModelCostPolicy
from packages.contracts.run import Stage
from packages.providers.nebius import NebiusConfig
from services.orchestrator.repair_flow import run_patch
from services.orchestrator.worker import WorkerResult
from services.persistence.baseline import BaselineJournal
from services.persistence.store import Lease, Store, StoreError


def tick_patch(
    store: Store,
    worker_id: UUID,
    config: NebiusConfig,
    cost_policy: ModelCostPolicy,
    *,
    allow_inference: bool = False,
    transport: httpx.BaseTransport | None = None,
    authorize: Callable[[Lease, int], None] | None = None,
) -> WorkerResult:
    """Claim PATCHING only and atomically hand a retained patch to VERIFYING.

    No baseline, model-selected execution, application or publication is implied.
    The existing gateway reserves cost/identity before I/O and sticky intents
    prevent ambiguous inference from being repeated after worker death.
    """
    if allow_inference is not True:
        raise StoreError("PATCH_INFERENCE_NOT_AUTHORIZED")
    lease = store.claim(worker_id, stages=(Stage.PATCHING,))
    if lease is None:
        return WorkerResult(status="IDLE")
    owner, run = store.claimed_run(lease)
    if authorize is not None:
        authorize(lease, run.version)
    action = BaselineJournal(store).accepted_action(run.run_id, owner)
    result = run_patch(
        store,
        lease,
        owner,
        config,
        action.execution_id,
        cost_policy=cost_policy,
        transport=transport,
        queue_verification=True,
    )
    return WorkerResult(
        status="PROCESSED", run_id=result.run_id, stage=result.current_stage
    )
