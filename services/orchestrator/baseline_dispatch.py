"""Admitted baseline submission with a durable one-use claim before provider I/O."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, Stage
from services.orchestrator.replay_dispatch import PreparedTestBackend
from services.persistence.baseline import BaselineJournal
from services.persistence.operations import OperationJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.commands import plan_pytest
from services.runner.receipts import plan_ref


class BaselineDispatchResult(BaseModel):
    """Submission metadata only; never a test outcome or permission to patch."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["SUBMITTED", "ALREADY_SUBMITTED", "SUBMISSION_UNKNOWN"]
    run_id: UUID
    execution_id: UUID
    operation_id: UUID | None
    plan_ref: ArtifactID
    evidence_origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    retryable: Literal[False] = False
    can_authorize_verification: Literal[False] = False


def dispatch_baseline(
    store: Store,
    lease: Lease,
    owner: UUID,
    execution_id: UUID,
    attestation: CapabilityAttestation,
    verifier: CapabilityVerifier,
    backend: PreparedTestBackend,
    *,
    expected_version: int,
    allow_live: bool = False,
) -> BaselineDispatchResult:
    """Submit a retained plan once; uncertainty never means permission to retry.

    Preparation/assessment are trusted deployment dependencies. This controller
    never creates their facts, runs host code or accepts unsigned baseline proof.
    Existing budget reservations and operation recovery/delivery are reused.
    """
    if backend.origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}:
        raise StoreError("BASELINE_BACKEND_INVALID")
    if backend.origin == "REMOTE_SERVICE" and allow_live is not True:
        raise StoreError("BASELINE_LIVE_NOT_AUTHORIZED")
    if type(expected_version) is not int or expected_version < 0:
        raise StoreError("STALE_VERSION")
    journal = BaselineJournal(store)
    action = journal.action(execution_id, owner)
    if action.plan.command_id not in {
        "PYTEST_FILES_SAVED_JUNIT_V1",
        "PYTEST_FILES_MEASURED_V1",
    }:
        raise StoreError("BASELINE_REPORT_PERSISTENCE_REQUIRED")
    reference = plan_ref(action.plan)

    def result(
        status: Literal["SUBMITTED", "ALREADY_SUBMITTED", "SUBMISSION_UNKNOWN"],
        operation: UUID | None = None,
    ) -> BaselineDispatchResult:
        return BaselineDispatchResult(
            status=status,
            run_id=lease.run_id,
            execution_id=execution_id,
            operation_id=operation,
            plan_ref=reference,
            evidence_origin=backend.origin,
        )

    # Assessment is rechecked under the DB clock and fence in the claim below.
    with store._connection() as conn:
        row = conn.execute("SELECT clock_timestamp() AS now").fetchone()
        if row is None or not isinstance(row["now"], datetime):
            raise StoreError("BASELINE_CLOCK_INVALID")
        assessed = verifier.assess(attestation, action.plan, now=row["now"])
        if (
            assessed.status != "CAPABILITY_REQUIREMENTS_MET"
            or attestation.payload.runner_id != backend.runner_id
            or attestation.payload.configuration_ref != backend.configuration_ref
        ):
            raise StoreError("BASELINE_ADMISSION_DENIED")
        prior = conn.execute(
            "SELECT from_version,to_version FROM baseline_dispatch_intents "
            "WHERE execution_id=%s",
            (execution_id,),
        ).fetchone()
    if prior is None:
        version = journal.reserve_dispatch(
            lease, owner, execution_id, expected_version=expected_version
        )
    else:
        if expected_version not in {prior["from_version"], prior["to_version"]}:
            raise StoreError("STALE_VERSION")
        version = journal.reserve_dispatch(
            lease, owner, execution_id, expected_version=int(str(prior["from_version"]))
        )
    with store._connection() as conn:
        run = journal._fence(conn, lease, owner, version)
        if (
            action.plan.request.run_id != run.run_id
            or run.current_stage != Stage.REPRODUCING
        ):
            raise StoreError("BASELINE_BINDING_DENIED")
        consent = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        if consent is None:
            raise StoreError("BASELINE_SCOPE_DENIED")
        plan_pytest(
            action.plan.request,
            run,
            RepositoryGrant.model_validate(consent["grant_json"]),
            owner,
        )
        row = conn.execute(
            "SELECT expires_at,clock_timestamp() AS now FROM baseline_actions "
            "WHERE execution_id=%s AND run_id=%s "
            "AND expires_at>clock_timestamp()",
            (execution_id, run.run_id),
        ).fetchone()
        if row is None or not isinstance(row["now"], datetime):
            raise StoreError("BASELINE_ACTION_EXPIRED")
        assessed = verifier.assess(attestation, action.plan, now=row["now"])
        if assessed.status != "CAPABILITY_REQUIREMENTS_MET":
            raise StoreError("BASELINE_ADMISSION_DENIED")
        binding = (
            reference,
            assessed.attestation_ref,
            backend.runner_id,
            backend.configuration_ref,
            backend.origin,
        )
        inserted = conn.execute(
            """INSERT INTO baseline_submissions
            (execution_id,plan_ref,capability_ref,runner_id,configuration_ref,origin,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
            RETURNING execution_id""",
            (execution_id, *binding, row["expires_at"]),
        ).fetchone()
        saved = conn.execute(
            "SELECT plan_ref,capability_ref,runner_id,configuration_ref,origin "
            "FROM baseline_submissions WHERE execution_id=%s "
            "AND expires_at>clock_timestamp()",
            (execution_id,),
        ).fetchone()
        if saved is None or tuple(saved.values()) != binding:
            raise StoreError("BASELINE_SUBMISSION_CONFLICT")
        if inserted is not None:
            if (
                conn.execute(
                    "SELECT execution_id FROM baseline_operation_records "
                    "WHERE execution_id=%s UNION ALL "
                    "SELECT execution_id FROM baseline_receipts "
                    "WHERE execution_id=%s LIMIT 1",
                    (execution_id, execution_id),
                ).fetchone()
                is not None
            ):
                raise StoreError("BASELINE_EXTERNAL_OPERATION_EXISTS")
            conn.execute(
                """INSERT INTO baseline_action_events
                (execution_id,kind,run_version,trace_id,worker_id)
                VALUES (%s,'SUBMISSION_CLAIMED',%s,%s,%s)""",
                (execution_id, run.version, run.trace_id, lease.worker_id),
            )
    operations = OperationJournal(store)
    if inserted is None:
        history = operations.history(execution_id, owner)
        known = next(
            (r.operation_id for r in history if r.operation_id is not None), None
        )
        return result("ALREADY_SUBMITTED" if known else "SUBMISSION_UNKNOWN", known)
    provider: Literal["NEBIUS_CONTREE", "TEST_TRANSPORT"] = (
        "NEBIUS_CONTREE" if backend.origin == "REMOTE_SERVICE" else "TEST_TRANSPORT"
    )
    try:
        operations.record(
            lease,
            owner,
            execution_id,
            provider=provider,
            phase="SUBMISSION_UNKNOWN",
            expected_version=run.version,
            require_new=True,
        )
    except StoreError as error:
        if str(error) != "OPERATION_ALREADY_RECORDED":
            raise
        return result("SUBMISSION_UNKNOWN")
    try:
        operation = backend.submit(action.plan, execution_id, action.source_tree_ref)
    except (TimeoutError, ConnectionError):
        return result("SUBMISSION_UNKNOWN")
    if not isinstance(operation, UUID):
        raise StoreError("BASELINE_OPERATION_INVALID")
    operations.record(
        lease,
        owner,
        execution_id,
        provider=provider,
        phase="OPERATION_KNOWN",
        expected_version=run.version,
        operation_id=operation,
    )
    return result("SUBMITTED", operation)
