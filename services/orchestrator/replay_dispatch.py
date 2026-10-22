"""One-shot admitted dispatch through an explicit trusted prepared-workspace port."""

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from packages.contracts.execution import TestCommandPlan
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, RunState, Stage
from packages.policies.repository import admit, allowed_entry
from services.persistence.replay import ReplayJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.receipts import plan_ref

Phase = Literal["CANDIDATE", "CLEAN_REPLAY"]
Origin = Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]


class PreparedTestBackend(Protocol):
    """Operator-injected adapter outside model/public control, never a shell tool.

    The adapter must independently bind its assessed runner/image/configuration,
    materialize the exact pinned source + patch + unchanged test in fresh remote
    compute, validate expected_tree_ref, and execute the immutable command plan.
    No default production implementation is installed by this controller.
    """

    origin: Origin
    runner_id: UUID
    configuration_ref: str

    def submit(
        self, plan: TestCommandPlan, execution_id: UUID, expected_tree_ref: str
    ) -> UUID:
        """Submit once with bounded network time; return a provider operation ID."""
        ...


class DispatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    status: Literal["SUBMITTED", "ALREADY_SUBMITTED", "SUBMISSION_UNKNOWN"]
    run_id: UUID
    execution_id: UUID
    phase: Phase
    operation_id: UUID | None
    plan_ref: ArtifactID
    evidence_origin: Origin
    observed_at: AwareDatetime
    trace_id: UUID
    policy_decision: Literal["CHECKED_REPLAY_DISPATCH"] = "CHECKED_REPLAY_DISPATCH"
    artifacts: tuple[ArtifactID, ...]
    error_class: None = None
    retryable: Literal[False] = False
    can_authorize_verification: Literal[False] = False


def dispatch_replay(
    store: Store,
    lease: Lease,
    owner: UUID,
    phase: Phase,
    attestation: CapabilityAttestation,
    verifier: CapabilityVerifier,
    backend: PreparedTestBackend,
    *,
    expected_version: int,
    allow_live: bool = False,
) -> DispatchResult:
    """Claim before I/O; never retry an uncertain or already attempted submission.

    Explicit live authorization is necessary but cannot waive admission. The
    backend is a trusted deployment dependency, never passed by browser/model.
    No HTTP client, credentials, local execution or automatic adapter discovery.
    """
    if phase not in {"CANDIDATE", "CLEAN_REPLAY"} or type(expected_version) is not int:
        raise StoreError("REPLAY_DISPATCH_INVALID")
    if backend.origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}:
        raise StoreError("REPLAY_BACKEND_INVALID")
    if backend.origin == "REMOTE_SERVICE" and allow_live is not True:
        raise StoreError("REPLAY_LIVE_NOT_AUTHORIZED")
    actions = ReplayJournal(store).load(lease.run_id, owner)
    plan = actions.candidate_plan if phase == "CANDIDATE" else actions.clean_plan
    execution_id = (
        actions.candidate_execution_id
        if phase == "CANDIDATE"
        else actions.clean_execution_id
    )
    reference = plan_ref(plan)

    def result(
        status: Literal["SUBMITTED", "ALREADY_SUBMITTED", "SUBMISSION_UNKNOWN"],
        operation_id: UUID | None = None,
    ) -> DispatchResult:
        return DispatchResult(
            status=status,
            run_id=lease.run_id,
            execution_id=execution_id,
            phase=phase,
            operation_id=operation_id,
            plan_ref=reference,
            evidence_origin=backend.origin,
            observed_at=datetime.now(UTC),
            trace_id=plan.trace_id,
            artifacts=(reference,),
        )

    with store._connection() as conn:
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (lease.run_id, owner),
        ).fetchone()
        job = conn.execute(
            "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
            "AND token=%s "
            "AND status='leased' AND leased_until>clock_timestamp() FOR UPDATE",
            (lease.run_id, lease.worker_id, lease.token),
        ).fetchone()
        if row is None or job is None:
            raise StoreError("LEASE_LOST")
        run = RunState.model_validate(row["checkpoint"])
        request = plan.request
        if (
            run.current_stage != Stage.VERIFYING
            or run.cancellation_requested
            or run.version != expected_version
            or not run.patch_attempt_refs
            or request.run_id != run.run_id
            or request.repository_id != run.repository_id
            or request.source_commit != run.source_commit
            or request.source_manifest_ref != run.source_manifest_ref
            or request.reproduction_test_ref != run.reproduction_test_ref
            or request.patch_ref != run.patch_attempt_refs[-1]
            or run.evidence_manifest_ref is not None
        ):
            raise StoreError("REPLAY_DISPATCH_BINDING_DENIED")
        scope = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        if scope is None:
            raise StoreError("REPLAY_SCOPE_DENIED")
        grant = RepositoryGrant.model_validate(scope["grant_json"])
        if not admit(grant, owner, run.repository_id, run.source_commit).allowed or any(
            not allowed_entry(grant, p, "100644") for p in request.test_paths
        ):
            raise StoreError("REPLAY_SCOPE_DENIED")
        clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
        if clock is None or not isinstance(clock["now"], datetime):
            raise StoreError("REPLAY_CLOCK_INVALID")
        assessment = verifier.assess(attestation, plan, now=clock["now"])
        if (
            assessment.status != "CAPABILITY_REQUIREMENTS_MET"
            or attestation.payload.runner_id != backend.runner_id
            or attestation.payload.configuration_ref != backend.configuration_ref
        ):
            raise StoreError("REPLAY_ADMISSION_DENIED")
        prior = conn.execute(
            "SELECT s.plan_ref,s.capability_ref,s.origin,r.operation_id "
            "FROM replay_submissions s "
            "LEFT JOIN replay_submission_results r USING(run_id,phase) "
            "WHERE s.run_id=%s AND s.phase=%s",
            (run.run_id, phase),
        ).fetchone()
        if prior is not None:
            if (
                prior["plan_ref"] != reference
                or prior["origin"] != backend.origin
                or prior["capability_ref"] != assessment.attestation_ref
            ):
                raise StoreError("REPLAY_DISPATCH_CONFLICT")
            operation = prior["operation_id"]
            return result(
                "ALREADY_SUBMITTED" if operation is not None else "SUBMISSION_UNKNOWN",
                None if operation is None else UUID(str(operation)),
            )
        conn.execute(
            "INSERT INTO replay_submissions"
            "(run_id,phase,plan_ref,capability_ref,origin) "
            "VALUES (%s,%s,%s,%s,%s)",
            (run.run_id, phase, reference, assessment.attestation_ref, backend.origin),
        )
        store._event(conn, run, "REPLAY_SUBMISSION_CLAIMED")
    # The intent has committed: a crash/fault from here must never repeat I/O.
    try:
        operation_id = backend.submit(plan, execution_id, actions.patched_tree_ref)
    except (TimeoutError, ConnectionError):
        return result("SUBMISSION_UNKNOWN")
    if not isinstance(operation_id, UUID):
        raise StoreError("REPLAY_OPERATION_INVALID")
    with store._connection() as conn:
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (lease.run_id, owner),
        ).fetchone()
        job = conn.execute(
            "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
            "AND token=%s "
            "AND status='leased' AND leased_until>clock_timestamp() FOR UPDATE",
            (lease.run_id, lease.worker_id, lease.token),
        ).fetchone()
        if row is None or job is None:
            raise StoreError("LEASE_LOST")
        after = RunState.model_validate(row["checkpoint"])
        if after != run:
            raise StoreError("STALE_VERSION")
        conn.execute(
            "INSERT INTO replay_submission_results(run_id,phase,operation_id) "
            "VALUES (%s,%s,%s)",
            (run.run_id, phase, operation_id),
        )
        store._event(conn, run, "REPLAY_OPERATION_BOUND")
    return result("SUBMITTED", operation_id)
