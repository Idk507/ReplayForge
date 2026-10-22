"""Connect durable producer receipts to the existing authenticated evidence reducer."""

from uuid import UUID

from packages.contracts.run import RunState
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
) -> RunState:
    """No caller-provided reports, provider IDs, signing keys or new execution.

    Duplicate exact delivery preserves the existing evidence bundle. Both
    journals are rechecked at the actual current run version, and the existing
    reducer rechecks scope/lease/CAS and report signatures before retaining.
    Test-origin receipts remain denied by that reducer; no VERIFIED or PR grant.
    """
    lease = Lease.model_validate(lease.model_dump())
    if type(expected_version) is not int:
        raise StoreError("PRODUCED_DELIVERY_INVALID")
    run = store.get(lease.run_id, owner)
    reconciles = run.evidence_manifest_ref is not None
    if run.version != expected_version + int(reconciles):
        raise StoreError("STALE_VERSION")
    for journal, phase in ((candidate, "CANDIDATE"), (clean, "CLEAN_REPLAY")):
        if (
            journal.store.dsn != store.dsn
            or journal.lease != lease
            or journal.owner != owner
            or journal.phase != phase
            or journal.version != expected_version
        ):
            raise StoreError("PRODUCED_DELIVERY_BINDING_DENIED")
    reports = []
    for journal in (candidate, clean):
        result = journal.at_version(run.version).completed()
        if not isinstance(result.signed, SignedReplayReceipt):
            raise StoreError("PRODUCED_DELIVERY_RECEIPT_DENIED")
        reports.append(ReplayReport(signed=result.signed, junit=result.junit))
    actions = ReplayJournal(store).load(lease.run_id, owner)
    return receive_replays(
        store,
        lease,
        owner,
        actions,
        reports[0],
        reports[1],
        verifier,
        workspace_sha256=workspace_sha256,
        artifacts=artifacts,
        expected_version=expected_version,
    )
