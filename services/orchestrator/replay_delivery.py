"""Worker-only replay delivery; retain evidence without granting a verdict."""

import hashlib
from datetime import datetime
from typing import Literal
from uuid import UUID

from packages.contracts.run import RunState, Stage
from services.persistence.baseline import BaselineJournal
from services.persistence.evidence import EvidenceStore
from services.persistence.replay import ReplayJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import ReceiptVerifier, canonical
from services.verifier.manifest import ArtifactInput, build_manifest
from services.verifier.replay import (
    ReplayActions,
    ReplayReport,
    canonical_actions,
    inspect_existing_suite,
    inspect_replay_pair,
)


def receive_replays(
    store: Store,
    lease: Lease,
    owner: UUID,
    actions: ReplayActions,
    candidate: ReplayReport,
    clean: ReplayReport,
    verifier: ReceiptVerifier,
    *,
    workspace_sha256: str,
    artifacts: tuple[ArtifactInput, ...] = (),
    expected_version: int,
    suite: ReplayReport | None = None,
) -> RunState:
    """Authenticate and atomically retain exact reports, checkpoint and event.

    Actions are trusted dispatch inputs, never a model/public request. The
    caller must sanitize reports before their collector signs them. This path
    neither submits work nor attests dispatch admission/fresh compute. It leaves
    VERIFYING unchanged, including when an authentic test report fails.
    """
    actions = ReplayActions.model_validate(actions.model_dump())
    candidate = ReplayReport.model_validate(candidate.model_dump())
    clean = ReplayReport.model_validate(clean.model_dump())
    if (actions.suite_plan is None) != (suite is None):
        raise StoreError("REPLAY_SUITE_DELIVERY_REQUIRED")
    if suite is not None:
        suite = ReplayReport.model_validate(suite.model_dump())
    replay_journal = ReplayJournal(store)
    if replay_journal.load(lease.run_id, owner) != actions:
        raise StoreError("REPLAY_RESERVATION_CONFLICT")
    reports: list[
        tuple[Literal["CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"], ReplayReport]
    ] = [("CANDIDATE", candidate), ("CLEAN_REPLAY", clean)]
    if suite is not None:
        reports.append(("EXISTING_SUITE", suite))
    if any(
        report.operation_id != replay_journal.operation_id(lease.run_id, owner, phase)
        for phase, report in reports
    ):
        raise StoreError("REPLAY_OPERATION_BINDING_DENIED")
    files = artifacts + (
        ArtifactInput(name="replay-actions.json", content=canonical_actions(actions)),
        ArtifactInput(
            name="candidate-receipt.json", content=canonical(candidate.signed)
        ),
        ArtifactInput(name="candidate-junit.xml", content=candidate.junit),
        ArtifactInput(name="clean-receipt.json", content=canonical(clean.signed)),
        ArtifactInput(name="clean-junit.xml", content=clean.junit),
    )
    if suite is not None:
        files += (
            ArtifactInput(name="suite-receipt.json", content=canonical(suite.signed)),
            ArtifactInput(name="suite-junit.xml", content=suite.junit),
        )
    manifest = build_manifest(workspace_sha256, files)
    run = store.get(lease.run_id, owner)
    request = actions.candidate_plan.request
    if (
        type(expected_version) is not int
        or run.current_stage != Stage.VERIFYING
        or run.cancellation_requested
        or not run.patch_attempt_refs
        or request.run_id != run.run_id
        or request.repository_id != run.repository_id
        or request.source_commit != run.source_commit
        or request.source_manifest_ref != run.source_manifest_ref
        or request.reproduction_test_ref != run.reproduction_test_ref
        or request.patch_ref != run.patch_attempt_refs[-1]
    ):
        raise StoreError("REPLAY_CHECKPOINT_DENIED")
    evidence = EvidenceStore(store)
    if run.evidence_manifest_ref is not None:
        # Exact historical reconciliation is a private read, never new authority.
        retained, _ = evidence.read(run.run_id, owner, run.evidence_manifest_ref)
        if retained != manifest or run.version != expected_version + 1:
            raise StoreError("REPLAY_DELIVERY_CONFLICT")
        return run
    claimed_owner, _ = store.claimed_run(lease)
    # Another exact delivery may commit after our read. The retaining
    # transaction owns CAS/idempotence; this read only establishes the fence.
    if claimed_owner != owner or run.version != expected_version:
        raise StoreError("STALE_VERSION")
    journal = BaselineJournal(store)
    baseline = journal.receipt(actions.baseline_execution_id, owner)
    baseline_action = journal.action(actions.baseline_execution_id, owner)
    journal.report(actions.baseline_execution_id, owner)
    if (
        run.baseline_result_ref
        != "sha256:" + hashlib.sha256(canonical(baseline)).hexdigest()
        or baseline.result.status != "MATCHED_SIGNED_REPORT"
        or baseline.signed.payload.origin != "REMOTE_SERVICE"
        or baseline.signed.payload.run_id != run.run_id
        or baseline_action.expected.test_name != actions.test_name
        or baseline_action.plan.request.reproduction_test_ref
        != run.reproduction_test_ref
        or baseline_action.plan.request.reproduction_path != request.reproduction_path
    ):
        raise StoreError("REPLAY_BASELINE_DENIED")
    with store._connection() as conn:
        clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
    if clock is None or not isinstance(clock["now"], datetime):
        raise StoreError("REPLAY_CLOCK_INVALID")
    inspections = inspect_replay_pair(
        actions, candidate, clean, verifier, now=clock["now"]
    )
    if any(r.receipt.origin != "REMOTE_SERVICE" for r in inspections):
        raise StoreError("REPLAY_TEST_EVIDENCE_DENIED")
    if suite is not None:
        suite_inspection = inspect_existing_suite(
            actions, suite, verifier, now=clock["now"]
        )
        if suite_inspection.receipt.origin != "REMOTE_SERVICE":
            raise StoreError("REPLAY_TEST_EVIDENCE_DENIED")
    # The existing transaction rechecks owner, consent, lease, stage and CAS.
    return evidence.retain(
        lease, owner, workspace_sha256, files, expected_version=expected_version
    )
