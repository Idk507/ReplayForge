"""One bounded baseline step over durable submission, collection and delivery."""

import ast
import re
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from packages.contracts.run import Stage
from packages.contracts.sandbox import RunnerError
from services.orchestrator.baseline_dispatch import dispatch_baseline
from services.orchestrator.produced_baseline_delivery import deliver_produced_baseline
from services.orchestrator.reproduction import (
    ReproductionArtifact,
    ReproductionHandoffError,
)
from services.persistence.baseline import BaselineJournal
from services.persistence.operations import OperationJournal
from services.persistence.reproduction import ReproductionJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.confined_baseline_provider import ConfinedBaselineProvider


class ConfinedBaselineResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal[
        "SUBMISSION_UNKNOWN", "PENDING", "TEST_EVIDENCE_RETAINED", "DELIVERED"
    ]
    run_id: UUID
    execution_id: UUID
    operation_id: UUID | None
    stage: Stage
    can_authorize_verification: Literal[False] = False


def _validate_reproduction(
    reproduction: ReproductionArtifact, provider: ConfinedBaselineProvider
) -> None:
    """Bind the intended test and assertion to retained inert source."""
    proposal = reproduction.proposal
    expected = provider.action.expected
    request = provider.action.plan.request
    marker = expected.assertion_marker
    signature = re.search(
        r"(?<![A-Z0-9_])" + re.escape(marker) + r"(?![A-Z0-9_])",
        proposal.failure_signature,
    )
    tree = ast.parse(proposal.test_source, mode="exec")
    tests = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == expected.test_name
    ]
    if (
        reproduction.artifact_ref() != request.reproduction_test_ref
        or proposal.target_path != request.reproduction_path
        or len(tests) != 1
        or signature is None
        or not any(
            isinstance(node, ast.Assert)
            and isinstance(node.msg, ast.Constant)
            and node.msg.value == marker
            for node in ast.walk(tests[0])
        )
    ):
        raise StoreError("BASELINE_REPRODUCTION_BINDING_DENIED")


def advance_confined_baseline(
    store: Store,
    lease: Lease,
    owner: UUID,
    provider: ConfinedBaselineProvider,
    *,
    allow_live: bool = False,
    queue_patch: bool = False,
    authorize: Callable[[Lease, int], None] | None = None,
) -> ConfinedBaselineResult:
    """Submit once or recover known work; perform at most one collection pass.

    The worker owns the lease and its heartbeat. A pending result does not
    acquire another lease, renew evidence, or repeat submission. Trusted
    deployment supplies a prepared workspace and assessed runtime.
    """
    if type(queue_patch) is not bool:
        raise StoreError("BASELINE_HANDOFF_INVALID")
    if provider.origin == "REMOTE_SERVICE" and allow_live is not True:
        raise StoreError("BASELINE_LIVE_NOT_AUTHORIZED")
    if provider.origin == "REMOTE_SERVICE" and not callable(authorize):
        raise StoreError("BASELINE_AUTHORIZATION_REQUIRED")
    claimed_owner, run = store.claimed_run(lease)
    if claimed_owner != owner or provider.run_id != lease.run_id:
        raise StoreError("BASELINE_BINDING_DENIED")
    provider.validate_run(run)
    if authorize is not None:
        authorize(lease, run.version)
    try:
        reproduction = ReproductionJournal(store).get(run.run_id, owner)
    except ReproductionHandoffError:
        raise StoreError("BASELINE_REPRODUCTION_BINDING_DENIED") from None
    except StoreError as error:
        if str(error) == "NOT_FOUND":
            raise StoreError("BASELINE_REPRODUCTION_UNAVAILABLE") from None
        raise
    _validate_reproduction(reproduction, provider)
    action = provider.action
    journal = BaselineJournal(store)
    journal.record_action(lease, owner, action, expected_version=run.version)
    with store._connection() as conn:
        prior = conn.execute(
            "SELECT to_version FROM baseline_dispatch_intents WHERE execution_id=%s",
            (action.execution_id,),
        ).fetchone()
    # The existing dispatcher performs the actual CAS/debit before backend I/O.
    # Any competing change causes this journal's fence to fail before POST.
    version = run.version + 1 if prior is None else int(str(prior["to_version"]))

    def outcome(
        status: Literal[
            "SUBMISSION_UNKNOWN", "PENDING", "TEST_EVIDENCE_RETAINED", "DELIVERED"
        ],
        operation: UUID | None,
        stage: Stage = Stage.REPRODUCING,
    ) -> ConfinedBaselineResult:
        return ConfinedBaselineResult(
            status=status,
            run_id=run.run_id,
            execution_id=action.execution_id,
            operation_id=operation,
            stage=stage,
        )

    with provider.open(store, lease, owner, expected_version=version) as (
        backend,
        producer,
    ):
        dispatch = dispatch_baseline(
            store,
            lease,
            owner,
            action.execution_id,
            provider.attestation,
            provider.verifier,
            backend,
            expected_version=run.version,
            allow_live=allow_live,
            authorize=authorize,
        )
        operation = dispatch.operation_id
        if operation is None:
            return outcome("SUBMISSION_UNKNOWN", None)
        if authorize is not None:
            authorize(lease, version)
        try:
            backend.collect_receipt(operation)
        except RunnerError as error:
            if str(error) == "S_REPORT_NOT_READY":
                return outcome("PENDING", operation)
            raise
        OperationJournal(store).record(
            lease,
            owner,
            action.execution_id,
            provider="NEBIUS_CONTREE"
            if provider.origin == "REMOTE_SERVICE"
            else "TEST_TRANSPORT",
            phase="COMPUTE_TERMINAL",
            expected_version=version,
            operation_id=operation,
        )
        if provider.origin == "TEST_TRANSPORT":
            return outcome("TEST_EVIDENCE_RETAINED", operation)
        accepted = deliver_produced_baseline(
            store,
            lease,
            owner,
            producer,
            provider.receipt_verifier(),
            queue_patch=queue_patch,
        )
        return outcome("DELIVERED", operation, accepted.current_stage)
