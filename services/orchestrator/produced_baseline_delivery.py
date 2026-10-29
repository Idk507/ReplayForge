"""Deliver a durably retained baseline producer receipt to the existing reducer."""

from uuid import UUID

from packages.contracts.run import RunState, Stage
from services.orchestrator.baseline_delivery import (
    accept_retained_baseline,
    receive_baseline,
)
from services.persistence.baseline import BaselineJournal
from services.persistence.baseline_producer import BaselineProducerJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import ReceiptVerifier, SignedReceipt, plan_ref


def deliver_produced_baseline(
    store: Store,
    lease: Lease,
    owner: UUID,
    journal: BaselineProducerJournal,
    verifier: ReceiptVerifier,
    *,
    queue_patch: bool = False,
) -> RunState:
    """Accept no caller-selected operation, report or claimed test result.

    Receipt production has already committed. Fresh acceptance retains the
    exact report through the existing baseline reducer. Duplicate acceptance
    uses its historical read reconciliation and grants no new action authority.
    """
    if type(queue_patch) is not bool:
        raise StoreError("BASELINE_HANDOFF_INVALID")
    if (
        journal.store.dsn != store.dsn
        or journal.lease != lease
        or journal.owner != owner
    ):
        raise StoreError("PRODUCED_DELIVERY_BINDING_DENIED")
    if journal.origin != "REMOTE_SERVICE":
        raise StoreError("BASELINE_TEST_EVIDENCE_DENIED")
    run = store.get(lease.run_id, owner)
    action = BaselineJournal(store).action(journal.binding.execution_id, owner)
    if (
        action.plan.request.run_id != run.run_id
        or plan_ref(action.plan) != journal.binding.plan_ref
        or action.source_tree_ref != journal.binding.tree_ref
    ):
        raise StoreError("PRODUCED_DELIVERY_BINDING_DENIED")
    if run.current_stage != Stage.REPRODUCING:
        return accept_retained_baseline(
            store, lease, owner, action.execution_id, verifier, queue_patch=queue_patch
        )
    if run.version != journal.version:
        raise StoreError("STALE_VERSION")
    result = journal.completed()
    if not isinstance(result.signed, SignedReceipt):
        raise StoreError("PRODUCED_DELIVERY_RECEIPT_DENIED")
    return receive_baseline(
        store,
        lease,
        owner,
        action.execution_id,
        result.signed,
        result.junit,
        verifier,
        queue_patch=queue_patch,
    )
