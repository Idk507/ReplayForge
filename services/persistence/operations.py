"""Inert provider-operation reconciliation journal; no submission or polling."""

import hashlib
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

import psycopg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, RunState
from packages.policies.repository import admit, allowed_entry
from services.persistence.baseline import (
    BaselineAction,
    BaselineJournal,
    _decode,
    _payload,
)
from services.persistence.store import Lease, Store, StoreError

Provider = Literal["NEBIUS_CONTREE", "TEST_TRANSPORT"]
Phase = Literal["SUBMISSION_UNKNOWN", "OPERATION_KNOWN", "COMPUTE_TERMINAL"]
PHASES: tuple[Phase, ...] = (
    "SUBMISSION_UNKNOWN",
    "OPERATION_KNOWN",
    "COMPUTE_TERMINAL",
)


class OperationRecord(BaseModel):
    """An adapter assertion, never a signed runner result or dispatch permit."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    execution_id: UUID
    run_id: UUID
    action_ref: ArtifactID
    provider: Provider
    phase: Phase
    operation_id: UUID | None = None
    recorded_at: AwareDatetime
    trace_id: UUID
    worker_id: UUID
    run_version: int = Field(ge=0, strict=True)
    evidence_origin: Literal["UNVERIFIED_ADAPTER_INPUT"] = "UNVERIFIED_ADAPTER_INPUT"
    can_authorize_execution: Literal[False] = False

    @model_validator(mode="after")
    def operation_binding(self) -> Self:
        if (self.phase == "SUBMISSION_UNKNOWN") != (self.operation_id is None):
            raise ValueError("OPERATION_ID_REQUIRED")
        return self


class OperationJournal:
    """Append-only lifecycle assertions for trusted control-plane adapters.

    Unknown submission is sticky until an operation ID is recovered externally.
    Nothing in this module issues network requests, refunds budget, advances a
    run stage or proves test success, isolation, cleanup or provider identity.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    @staticmethod
    def _history(
        conn: psycopg.Connection[dict[str, object]],
        execution_id: UUID,
        run_id: UUID,
        action_digest: str,
    ) -> tuple[OperationRecord, ...]:
        rows = conn.execute(
            "SELECT sequence,phase,provider,operation_id,payload,digest "
            "FROM baseline_operation_records "
            "WHERE execution_id=%s ORDER BY sequence LIMIT 4",
            (execution_id,),
        ).fetchall()
        if len(rows) > 3:
            raise StoreError("OPERATION_INTEGRITY")
        history: list[OperationRecord] = []
        for i, row in enumerate(rows):
            record = _decode(row["payload"], row["digest"], OperationRecord)
            if (
                record.phase != PHASES[i]
                or row["phase"] != record.phase
                or row["sequence"] != i + 1
                or row["provider"] != record.provider
                or row["operation_id"] != record.operation_id
                or record.execution_id != execution_id
                or record.run_id != run_id
                or record.action_ref != "sha256:" + action_digest
                or (history and record.provider != history[0].provider)
                or (i == 2 and record.operation_id != history[1].operation_id)
            ):
                raise StoreError("OPERATION_INTEGRITY")
            history.append(record)
        return tuple(history)

    def history(self, execution_id: UUID, owner: UUID) -> tuple[OperationRecord, ...]:
        """Owner-scoped historical metadata; no current execution authority."""
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT a.run_id,a.digest FROM baseline_actions a "
                "JOIN runs r ON r.id=a.run_id "
                "WHERE a.execution_id=%s AND r.owner_id=%s "
                "AND a.expires_at>clock_timestamp()",
                (execution_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            run_id = row["run_id"]
            if not isinstance(run_id, UUID):
                raise StoreError("OPERATION_INTEGRITY")
            return self._history(conn, execution_id, run_id, str(row["digest"]))

    def record(
        self,
        lease: Lease,
        owner: UUID,
        execution_id: UUID,
        *,
        provider: Provider,
        phase: Phase,
        expected_version: int,
        operation_id: UUID | None = None,
        require_new: bool = False,
    ) -> OperationRecord:
        """Retain one bounded assertion under a live lease, without dispatch.

        Binding/terminal evidence may arrive after cancellation or revoked
        consent. Allow retaining it for recovery; it cannot schedule more work.
        """
        lease = Lease.model_validate(lease.model_dump())
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (lease.run_id, owner),
            ).fetchone()
            job = conn.execute(
                "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
                "AND token=%s AND status='leased' AND leased_until>clock_timestamp() "
                "FOR UPDATE",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None or job is None:
                raise StoreError("LEASE_LOST")
            run = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or run.version != expected_version:
                raise StoreError("STALE_VERSION")
            row = conn.execute(
                "SELECT a.payload,a.digest,i.action_digest,i.to_version,"
                "clock_timestamp() AS now FROM baseline_actions a "
                "JOIN baseline_dispatch_intents i ON i.execution_id=a.execution_id "
                "WHERE a.execution_id=%s AND a.run_id=%s "
                "AND a.expires_at>clock_timestamp()",
                (execution_id, run.run_id),
            ).fetchone()
            if row is None:
                raise StoreError("OPERATION_RESERVATION_REQUIRED")
            action = _decode(row["payload"], row["digest"], BaselineAction)
            if (
                row["action_digest"] != row["digest"]
                or int(str(row["to_version"])) > run.version
                or action.execution_id != execution_id
            ):
                raise StoreError("OPERATION_INTEGRITY")
            now = row["now"]
            if not isinstance(now, datetime):
                raise StoreError("OPERATION_CLOCK")
            record = OperationRecord(
                execution_id=execution_id,
                run_id=run.run_id,
                action_ref="sha256:" + str(row["digest"]),
                provider=provider,
                phase=phase,
                operation_id=operation_id,
                recorded_at=now,
                trace_id=run.trace_id,
                worker_id=lease.worker_id,
                run_version=run.version,
            )
            history = self._history(conn, execution_id, run.run_id, str(row["digest"]))
            for previous in history:
                if previous.phase == record.phase:
                    if (
                        previous.provider != record.provider
                        or previous.operation_id != record.operation_id
                    ):
                        raise StoreError("OPERATION_CONFLICT")
                    if require_new:
                        raise StoreError("OPERATION_ALREADY_RECORDED")
                    return previous
            if len(history) == 3 or record.phase != PHASES[len(history)]:
                raise StoreError("OPERATION_ORDER_DENIED")
            if history and (
                record.provider != history[0].provider
                or (len(history) == 2 and operation_id != history[1].operation_id)
            ):
                raise StoreError("OPERATION_CONFLICT")
            if not history:
                # Starting an uncertain submission needs all current admission
                # bindings. Later cleanup assertions do not authorize new work.
                BaselineJournal._fence(conn, lease, owner, expected_version)
                consent = conn.execute(
                    "SELECT grant_json FROM repository_grants "
                    "WHERE id=%s AND owner_id=%s",
                    (run.repository_id, owner),
                ).fetchone()
                if consent is None:
                    raise StoreError("BASELINE_SCOPE_DENIED")
                grant = RepositoryGrant.model_validate(consent["grant_json"])
                request = action.plan.request
                if (
                    not admit(
                        grant, owner, run.repository_id, run.source_commit
                    ).allowed
                    or request.run_id != run.run_id
                    or request.phase != "BASELINE"
                    or request.repository_id != run.repository_id
                    or request.source_commit != run.source_commit
                    or request.source_manifest_ref != run.source_manifest_ref
                    or request.reproduction_test_ref != run.reproduction_test_ref
                    or any(
                        not allowed_entry(grant, p, "100644")
                        for p in request.test_paths
                    )
                ):
                    raise StoreError("BASELINE_BINDING_DENIED")
                if (
                    conn.execute(
                        "SELECT execution_id FROM baseline_receipts "
                        "WHERE execution_id=%s",
                        (execution_id,),
                    ).fetchone()
                    is not None
                ):
                    raise StoreError("BASELINE_ALREADY_COMPLETED")
            body = _payload(record)
            conn.execute(
                "INSERT INTO baseline_operation_records "
                "(execution_id,sequence,phase,provider,operation_id,payload,digest) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    execution_id,
                    len(history) + 1,
                    record.phase,
                    record.provider,
                    operation_id,
                    body,
                    hashlib.sha256(body).hexdigest(),
                ),
            )
            return record
