"""Connect durable producer receipts to the existing authenticated evidence reducer."""

from uuid import UUID

from packages.contracts.run import RunState
from services.orchestrator.production_verification import ProductionGateProducer
from services.orchestrator.replay_delivery import receive_replays
from services.persistence.producer import ProducerJournal
from services.persistence.replay import ReplayJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import ReceiptVerifier, SignedReplayReceipt
from services.verifier.manifest import ArtifactInput
from services.verifier.replay import ReplayReport


def deliver_produced_replays(
    store: Store,
    lease: Lease,
    owner: UUID,
    candidate: ProducerJournal,
    clean: ProducerJournal,
    verifier: ReceiptVerifier,
    *,
    workspace_sha256: str,
    expected_version: int,
    artifacts: tuple[ArtifactInput, ...] = (),
    suite: ProducerJournal | None = None,
    production: ProductionGateProducer | None = None,
    defer_finalization: bool = False,
) -> RunState:
    """No caller-provided reports, provider IDs, signing keys or new execution.

    Duplicate exact delivery preserves the existing evidence bundle. Both
    journals are rechecked at the actual current run version, and the existing
    reducer rechecks scope/lease/CAS and report signatures before retaining.
    Test-origin receipts remain denied by that reducer; no VERIFIED or PR grant.
    """
    lease = Lease.model_validate(lease.model_dump())
    if (
        type(expected_version) is not int
        or type(defer_finalization) is not bool
        or (defer_finalization and production is None)
    ):
        raise StoreError("PRODUCED_DELIVERY_INVALID")
    run = store.get(lease.run_id, owner)
    actions = ReplayJournal(store).load(lease.run_id, owner)
    if (actions.suite_plan is None) != (suite is None):
        raise StoreError("PRODUCED_DELIVERY_SUITE_REQUIRED")
    reconciles = run.evidence_manifest_ref is not None
    if run.version != expected_version + int(reconciles):
        raise StoreError("STALE_VERSION")
    expected_journals = [(candidate, "CANDIDATE"), (clean, "CLEAN_REPLAY")]
    if suite is not None:
        expected_journals.append((suite, "EXISTING_SUITE"))
    for journal, phase in expected_journals:
        if journal is None:
            raise StoreError("PRODUCED_DELIVERY_SUITE_REQUIRED")
        if (
            journal.store.dsn != store.dsn
            or journal.lease != lease
            or journal.owner != owner
            or journal.phase != phase
            or journal.version != expected_version
        ):
            raise StoreError("PRODUCED_DELIVERY_BINDING_DENIED")
    reports = []
    for journal, _ in expected_journals:
        if journal is None:
            raise StoreError("PRODUCED_DELIVERY_SUITE_REQUIRED")
        result = journal.at_version(run.version).completed()
        if not isinstance(result.signed, SignedReplayReceipt):
            raise StoreError("PRODUCED_DELIVERY_RECEIPT_DENIED")
        reports.append(
            ReplayReport(
                operation_id=result.operation_id,
                signed=result.signed,
                junit=result.junit,
            )
        )
    final_artifacts = artifacts
    if production is not None:
        final_artifacts += production.artifacts(lease, owner)
    retained = receive_replays(
        store,
        lease,
        owner,
        actions,
        reports[0],
        reports[1],
        verifier,
        workspace_sha256=workspace_sha256,
        artifacts=final_artifacts,
        expected_version=expected_version,
        suite=(
            None
            if suite is None
            else ReplayReport(
                operation_id=reports[2].operation_id,
                signed=reports[2].signed,
                junit=reports[2].junit,
            )
        ),
    )
    if production is None:
        return retained
    if defer_finalization:
        return retained
    return production.finalize(lease, owner, expected_version=retained.version)
