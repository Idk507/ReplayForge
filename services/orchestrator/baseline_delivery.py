"""Explicit signed baseline delivery and restart recovery; no execution/inference."""

import hashlib
from uuid import UUID

from packages.contracts.run import RunState, Stage
from services.persistence.baseline import BaselineJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import ReceiptVerifier, SignedReceipt, canonical


def _reconcile(store: Store, owner: UUID, lease: Lease, execution_id: UUID) -> RunState:
    """Read an already committed result; grant no new stage or action authority."""
    run = store.get(lease.run_id, owner)
    journal = BaselineJournal(store)
    retained = journal.receipt(execution_id, owner)
    journal.report(execution_id, owner)  # Detect damaged or missing retained bytes.
    if retained.signed.payload.run_id != run.run_id:
        raise StoreError("BASELINE_BINDING_DENIED")
    reference = "sha256:" + hashlib.sha256(canonical(retained)).hexdigest()
    if (
        run.current_stage in {Stage.PATCHING, Stage.VERIFYING}
        and run.baseline_result_ref == reference
    ) or (
        run.current_stage == Stage.INCONCLUSIVE
        and run.failure_reason == "INVALID_REPRODUCTION"
        and retained.result.status == "REJECTED_SIGNED_REPORT"
    ):
        return run
    raise StoreError("BASELINE_DELIVERY_STAGE_DENIED")


def accept_retained_baseline(
    store: Store,
    lease: Lease,
    owner: UUID,
    execution_id: UUID,
    verifier: ReceiptVerifier,
) -> RunState:
    """Resume after receipt commit, using retained bytes and the trusted reducer.

    Fresh acceptance rechecks current keys, consent, reservation, signature and
    live lease inside the existing reducer. Reconciliation only reads an exact
    previously committed result and never calls a provider or PatchAgent.
    """
    run = store.get(lease.run_id, owner)
    if run.current_stage != Stage.REPRODUCING:
        return _reconcile(store, owner, lease, execution_id)
    journal = BaselineJournal(store)
    report = journal.report(execution_id, owner)
    try:
        return journal.accept(
            lease, owner, execution_id, report, verifier, expected_version=run.version
        )
    except StoreError as error:
        if str(error) not in {"STALE_VERSION", "BASELINE_STAGE_DENIED", "LEASE_LOST"}:
            raise
        return _reconcile(store, owner, lease, execution_id)


def receive_baseline(
    store: Store,
    lease: Lease,
    owner: UUID,
    execution_id: UUID,
    signed: SignedReceipt,
    junit: bytes,
    verifier: ReceiptVerifier,
) -> RunState:
    """Receive an external collector's signed report, retain, then reduce it.

    Not a public endpoint or signing service. The producer must redact before
    signing and independently observe runner facts. Duplicate/conflicting
    deliveries cannot replace report bytes, reset source or trigger inference.
    """
    if type(junit) is not bytes or not 1 <= len(junit) <= 131072:
        raise StoreError("BASELINE_REPORT_LIMIT")
    signed = SignedReceipt.model_validate(signed.model_dump())
    run = store.get(lease.run_id, owner)
    journal = BaselineJournal(store)
    if run.current_stage == Stage.REPRODUCING:
        try:
            journal.retain(
                lease,
                owner,
                execution_id,
                signed,
                junit,
                verifier,
                expected_version=run.version,
            )
        except StoreError as error:
            if str(error) not in {
                "STALE_VERSION",
                "BASELINE_STAGE_DENIED",
                "LEASE_LOST",
            }:
                raise
            _reconcile(store, owner, lease, execution_id)
    retained = journal.receipt(execution_id, owner)
    if (
        canonical(retained.signed) != canonical(signed)
        or journal.report(execution_id, owner) != junit
    ):
        raise StoreError("BASELINE_CONFLICT")
    return accept_retained_baseline(store, lease, owner, execution_id, verifier)
