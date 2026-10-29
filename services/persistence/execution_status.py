"""Owner-scoped read-only execution state; never performs provider recovery I/O."""

import hashlib
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from packages.contracts.run import ArtifactID
from services.persistence.operations import OperationJournal
from services.persistence.replay import ReplayJournal
from services.persistence.store import Store, StoreError
from services.runner.observations import ReplayObservation
from services.runner.receipts import plan_ref

ExecutionPhase = Literal["BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"]
ExecutionStatusName = Literal[
    "NOT_PLANNED",
    "PLANNED",
    "SUBMISSION_UNKNOWN",
    "OPERATION_KNOWN",
    "OPERATION_PENDING",
    "PROVIDER_TERMINAL_UNVERIFIED",
    "RECEIPT_RETAINED_UNVERIFIED",
    "EXPIRED",
    "INTEGRITY_FAILURE",
]
EvidenceOrigin = Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]


class ExecutionPhaseStatus(BaseModel):
    """Sanitized status metadata that grants no recovery or verification authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: ExecutionPhase
    status: ExecutionStatusName
    execution_id: UUID | None = None
    operation_id: UUID | None = None
    plan_ref: ArtifactID | None = None
    origin: EvidenceOrigin | None = None
    observed_at: AwareDatetime | None = None
    can_authorize_execution: Literal[False] = False
    can_authorize_verification: Literal[False] = False


class ExecutionStatusSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: UUID
    phases: tuple[ExecutionPhaseStatus, ...]


def _status_from_observation(
    payload: object, digest: object, operation_id: object
) -> tuple[ExecutionStatusName, datetime | None]:
    if payload is None:
        return "OPERATION_KNOWN", None
    if (
        not isinstance(payload, (bytes, bytearray, memoryview))
        or not isinstance(digest, str)
        or hashlib.sha256(bytes(payload)).hexdigest() != digest
    ):
        return "INTEGRITY_FAILURE", None
    try:
        observation = ReplayObservation.model_validate_json(bytes(payload))
        if observation.operation_id != operation_id:
            return "INTEGRITY_FAILURE", None
    except (ValueError, TypeError):
        return "INTEGRITY_FAILURE", None
    if observation.status == "PENDING":
        return "OPERATION_PENDING", observation.observed_at
    if observation.status == "TERMINAL_NO_REPORT":
        return "PROVIDER_TERMINAL_UNVERIFIED", observation.observed_at
    return "PROVIDER_TERMINAL_UNVERIFIED", observation.observed_at


def execution_status(
    store: Store, run_id: UUID, owner: UUID
) -> ExecutionStatusSnapshot:
    """Read journal state only; never polls, repairs, retries, or changes a run."""
    try:
        store.get(run_id, owner)
    except StoreError:
        raise
    phases: list[ExecutionPhaseStatus] = []
    with store._connection() as conn:
        rows = conn.execute(
            "SELECT a.execution_id,a.plan_ref,a.digest,s.origin,"
            "s.expires_at>clock_timestamp() AS active,"
            "EXISTS(SELECT 1 FROM baseline_receipts r "
            "WHERE r.execution_id=a.execution_id) "
            "AS has_receipt FROM baseline_actions a JOIN runs u ON u.id=a.run_id "
            "LEFT JOIN baseline_submissions s USING(execution_id) "
            "WHERE a.run_id=%s AND u.owner_id=%s ORDER BY a.created_at LIMIT 3",
            (run_id, owner),
        ).fetchall()
        for row in rows:
            execution = UUID(str(row["execution_id"]))
            try:
                history = OperationJournal(store)._history(
                    conn, execution, run_id, str(row["digest"])
                )
            except StoreError:
                phases.append(
                    ExecutionPhaseStatus(
                        phase="BASELINE",
                        status="INTEGRITY_FAILURE",
                        execution_id=execution,
                        plan_ref=cast(ArtifactID | None, row["plan_ref"]),
                        origin=cast(EvidenceOrigin | None, row["origin"]),
                    )
                )
                continue
            latest = history[-1] if history else None
            operation: UUID | None = next(
                (
                    record.operation_id
                    for record in reversed(history)
                    if record.operation_id
                ),
                None,
            )
            if row["origin"] is not None and not row["active"]:
                status: ExecutionStatusName = "EXPIRED"
            elif row["has_receipt"]:
                status = "RECEIPT_RETAINED_UNVERIFIED"
            elif latest is None or latest.phase == "SUBMISSION_UNKNOWN":
                status = (
                    "SUBMISSION_UNKNOWN" if row["origin"] is not None else "PLANNED"
                )
            elif latest.phase == "OPERATION_KNOWN":
                status = "OPERATION_KNOWN"
            else:
                status = "PROVIDER_TERMINAL_UNVERIFIED"
            phases.append(
                ExecutionPhaseStatus(
                    phase="BASELINE",
                    status=status,
                    execution_id=execution,
                    operation_id=operation,
                    plan_ref=cast(ArtifactID | None, row["plan_ref"]),
                    origin=cast(EvidenceOrigin | None, row["origin"]),
                    observed_at=latest.recorded_at if latest else None,
                )
            )
        try:
            actions = ReplayJournal(store).load(run_id, owner)
        except StoreError as error:
            if str(error) != "REPLAY_RESERVATION_REQUIRED":
                raise
            actions = None
        plan_map = {
            "CANDIDATE": None if actions is None else actions.candidate_plan,
            "EXISTING_SUITE": None if actions is None else actions.suite_plan,
            "CLEAN_REPLAY": None if actions is None else actions.clean_plan,
        }
        execution_map = {
            "CANDIDATE": None if actions is None else actions.candidate_execution_id,
            "EXISTING_SUITE": None if actions is None else actions.suite_execution_id,
            "CLEAN_REPLAY": None if actions is None else actions.clean_execution_id,
        }
        for phase in ("CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"):
            plan = plan_map[phase]
            replay_execution: UUID | None = execution_map[phase]
            if plan is None or replay_execution is None:
                phases.append(ExecutionPhaseStatus(phase=phase, status="NOT_PLANNED"))
                continue
            replay_row = conn.execute(
                "SELECT s.plan_ref,s.origin,s.created_at,r.operation_id,"
                "o.payload AS observation,o.digest AS observation_digest,"
                "p.expires_at AS preparation_expiry,"
                "p.expires_at>clock_timestamp() AS preparation_active,"
                "EXISTS(SELECT 1 FROM replay_produced_receipts x "
                "WHERE x.run_id=s.run_id AND x.phase=s.phase) AS has_receipt "
                "FROM replay_submissions s LEFT JOIN replay_submission_results r "
                "USING(run_id,phase) LEFT JOIN replay_observations o "
                "USING(run_id,phase) "
                "LEFT JOIN replay_preparations p USING(run_id,phase) "
                "JOIN runs u ON u.id=s.run_id WHERE s.run_id=%s AND s.phase=%s "
                "AND u.owner_id=%s",
                (run_id, phase, owner),
            ).fetchone()
            if replay_row is None:
                status = "PLANNED"
                operation = None
                observed: datetime | None = None
                origin = None
            else:
                operation = (
                    None
                    if replay_row["operation_id"] is None
                    else UUID(str(replay_row["operation_id"]))
                )
                origin = cast(EvidenceOrigin | None, replay_row["origin"])
                observed = None
                if replay_row["operation_id"] is None:
                    status = "SUBMISSION_UNKNOWN"
                else:
                    status, observed = _status_from_observation(
                        replay_row["observation"],
                        replay_row["observation_digest"],
                        replay_row["operation_id"],
                    )
                    if replay_row["has_receipt"]:
                        status = "RECEIPT_RETAINED_UNVERIFIED"
                    elif (
                        replay_row["preparation_expiry"] is not None
                        and not replay_row["preparation_active"]
                    ):
                        status = "EXPIRED"
            phases.append(
                ExecutionPhaseStatus(
                    phase=phase,
                    status=status,
                    execution_id=replay_execution,
                    operation_id=operation,
                    plan_ref=plan_ref(plan),
                    origin=origin,
                    observed_at=(
                        observed
                        or (
                            None
                            if replay_row is None
                            else cast(datetime, replay_row["created_at"])
                        )
                    ),
                )
            )
    return ExecutionStatusSnapshot(run_id=run_id, phases=tuple(phases))
