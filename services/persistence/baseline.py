"""Baseline journal and atomic command reservations; no external dispatch."""

import hashlib
from datetime import datetime
from typing import TypeVar
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from packages.contracts.execution import TestCommandPlan
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, RunState, Stage
from packages.policies.repository import admit, allowed_entry
from packages.policies.transitions import transition
from services.persistence.model_budget import debit_baseline, reservation_transaction
from services.persistence.store import Lease, Store, StoreError
from services.runner.baseline import FailureExpectation
from services.runner.commands import plan_pytest
from services.runner.receipts import (
    ReceiptVerifier,
    SignedBaselineInspection,
    SignedReceipt,
    canonical,
    plan_ref,
)


class BaselineAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    execution_id: UUID
    plan: TestCommandPlan
    source_tree_ref: ArtifactID
    expected: FailureExpectation


class RetainedReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    signed: SignedReceipt
    result: SignedBaselineInspection
    accepted_at: AwareDatetime


T = TypeVar("T", bound=BaseModel)


def _payload(model: BaseModel) -> bytes:
    body = canonical(model)
    if len(body) > 16384:
        raise StoreError("BASELINE_LIMIT")
    return body


def _decode(payload: object, digest: object, model: type[T]) -> T:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise StoreError("BASELINE_INTEGRITY")
    body = bytes(payload)
    if len(body) > 16384 or hashlib.sha256(body).hexdigest() != digest:
        raise StoreError("BASELINE_INTEGRITY")
    try:
        parsed = model.model_validate_json(body)
        if _payload(parsed) != body:
            raise StoreError("BASELINE_INTEGRITY")
        return parsed
    except (ValidationError, ValueError):
        raise StoreError("BASELINE_INTEGRITY") from None


class BaselineJournal:
    """Trusted reducer-facing adapter; runtime needs SELECT/INSERT only.

    Planning/retention is not execution acceptance. Only accept() may advance
    the stage, after revalidating a reserved, authenticated remote report.
    Test-transport receipts cannot open PATCHING through this journal.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def accept(
        self,
        lease: Lease,
        owner: UUID,
        execution_id: UUID,
        junit: bytes,
        verifier: ReceiptVerifier,
        *,
        expected_version: int,
    ) -> RunState:
        """Reduce a retained, reserved remote baseline into a durable checkpoint.

        Reverify actual report bytes under the current trusted key configuration.
        A test-transport receipt never opens PATCHING. Failed reproduction setup
        closes INCONCLUSIVE; neither path claims a verified repair. A replay
        reads the checkpoint, rather than reaccepting or dispatching work.
        """
        lease = Lease.model_validate(lease.model_dump())
        with self._store._connection() as conn:
            run = self._fence(conn, lease, owner, expected_version)
            row = conn.execute(
                """SELECT a.payload AS action_payload,a.digest AS action_digest,
                s.payload,s.digest,clock_timestamp() AS now
                FROM baseline_actions a JOIN baseline_receipts s
                ON s.execution_id=a.execution_id JOIN baseline_dispatch_intents i
                ON i.execution_id=a.execution_id AND i.action_digest=a.digest
                WHERE a.execution_id=%s AND a.run_id=%s
                AND a.expires_at>clock_timestamp()
                AND s.expires_at>clock_timestamp()""",
                (execution_id, run.run_id),
            ).fetchone()
            if row is None:
                raise StoreError("BASELINE_RESERVED_RECEIPT_REQUIRED")
            action = _decode(
                row["action_payload"], row["action_digest"], BaselineAction
            )
            retained = _decode(row["payload"], row["digest"], RetainedReceipt)
            request = action.plan.request
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if consent is None:
                raise StoreError("BASELINE_SCOPE_DENIED")
            grant = RepositoryGrant.model_validate(consent["grant_json"])
            if (
                not admit(grant, owner, run.repository_id, run.source_commit).allowed
                or retained.signed.payload.origin != "REMOTE_SERVICE"
                or request.run_id != run.run_id
                or request.repository_id != run.repository_id
                or request.source_commit != run.source_commit
                or request.source_manifest_ref != run.source_manifest_ref
                or request.reproduction_test_ref != run.reproduction_test_ref
                or any(
                    not allowed_entry(grant, p, "100644") for p in request.test_paths
                )
            ):
                raise StoreError("BASELINE_BINDING_DENIED")
            now = row["now"]
            if not isinstance(now, datetime):
                raise StoreError("BASELINE_CLOCK")
            result = verifier.inspect_baseline(
                retained.signed,
                action.plan,
                junit,
                execution_id=execution_id,
                source_tree_ref=action.source_tree_ref,
                expected=action.expected,
                now=now,
            )
            data = run.model_dump()
            data["failure_reason"] = "INVALID_REPRODUCTION"
            target = Stage.INCONCLUSIVE
            if result.status == "MATCHED_SIGNED_REPORT":
                data["baseline_result_ref"] = "sha256:" + str(row["digest"])
                data["failure_reason"] = None
                target = Stage.PATCHING
            updated = transition(
                RunState.model_validate(data), target, expected_version=expected_version
            )
            self._store._save(conn, updated)
            if target == Stage.INCONCLUSIVE:
                conn.execute(
                    "UPDATE job_leases SET status='done',token=NULL WHERE run_id=%s",
                    (run.run_id,),
                )
            return updated

    @staticmethod
    def _fence(
        conn: psycopg.Connection[dict[str, object]],
        lease: Lease,
        owner: UUID,
        version: int,
    ) -> RunState:
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (lease.run_id, owner),
        ).fetchone()
        job = conn.execute(
            """SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s
            AND token=%s AND status='leased' AND leased_until>clock_timestamp()
            FOR UPDATE""",
            (lease.run_id, lease.worker_id, lease.token),
        ).fetchone()
        if row is None or job is None:
            raise StoreError("LEASE_LOST")
        state = RunState.model_validate(row["checkpoint"])
        if type(version) is not int or state.version != version:
            raise StoreError("STALE_VERSION")
        if state.current_stage != Stage.REPRODUCING or state.cancellation_requested:
            raise StoreError("BASELINE_STAGE_DENIED")
        return state

    def record_action(
        self,
        lease: Lease,
        owner: UUID,
        action: BaselineAction,
        *,
        expected_version: int,
    ) -> str:
        lease = Lease.model_validate(lease.model_dump())
        action = BaselineAction.model_validate(action.model_dump())
        body = _payload(action)
        digest = hashlib.sha256(body).hexdigest()
        with self._store._connection() as conn:
            run = self._fence(conn, lease, owner, expected_version)
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if consent is None or action.plan.request.phase != "BASELINE":
                raise StoreError("BASELINE_SCOPE_DENIED")
            plan_pytest(
                action.plan.request,
                run,
                RepositoryGrant.model_validate(consent["grant_json"]),
                owner,
            )
            existing = conn.execute(
                """SELECT digest FROM baseline_actions WHERE execution_id=%s
                OR (run_id=%s AND plan_ref=%s)""",
                (action.execution_id, run.run_id, plan_ref(action.plan)),
            ).fetchone()
            if existing is not None:
                if existing["digest"] != digest:
                    raise StoreError("BASELINE_CONFLICT")
                return "sha256:" + digest
            count = conn.execute(
                "SELECT count(*) AS n FROM baseline_actions WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
            if count is None or int(str(count["n"])) >= 6:
                raise StoreError("BASELINE_ACTION_LIMIT")
            conn.execute(
                """INSERT INTO baseline_actions
                (execution_id,run_id,plan_ref,digest,payload)
                VALUES (%s,%s,%s,%s,%s)""",
                (action.execution_id, run.run_id, plan_ref(action.plan), digest, body),
            )
            conn.execute(
                """INSERT INTO baseline_action_events
                (execution_id,kind,run_version,trace_id,worker_id)
                VALUES (%s,'PLANNED',%s,%s,%s)""",
                (action.execution_id, run.version, run.trace_id, lease.worker_id),
            )
        return "sha256:" + digest

    def action(self, execution_id: UUID, owner: UUID) -> BaselineAction:
        with self._store._connection() as conn:
            row = conn.execute(
                """SELECT a.payload,a.digest FROM baseline_actions a
                JOIN runs r ON r.id=a.run_id WHERE a.execution_id=%s AND r.owner_id=%s
                AND a.expires_at>clock_timestamp()""",
                (execution_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        action = _decode(row["payload"], row["digest"], BaselineAction)
        if action.execution_id != execution_id:
            raise StoreError("BASELINE_INTEGRITY")
        return action

    def reserve_dispatch(
        self, lease: Lease, owner: UUID, execution_id: UUID, *, expected_version: int
    ) -> int:
        """Atomically debit one command and its planned duration; never dispatch.

        A replay reconciles the existing reservation under a live lease. It is
        not permission to resubmit an external operation with unknown outcome.
        """
        lease = Lease.model_validate(lease.model_dump())
        if type(expected_version) is not int or expected_version < 0:
            raise StoreError("STALE_VERSION")
        with reservation_transaction(self._store) as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (lease.run_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("LEASE_LOST")
            current = RunState.model_validate(row["checkpoint"])
            run = self._fence(conn, lease, owner, current.version)
            row = conn.execute(
                """SELECT payload,digest FROM baseline_actions WHERE execution_id=%s
                AND run_id=%s AND expires_at>clock_timestamp()""",
                (execution_id, run.run_id),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            action = _decode(row["payload"], row["digest"], BaselineAction)
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if consent is None:
                raise StoreError("BASELINE_SCOPE_DENIED")
            grant = RepositoryGrant.model_validate(consent["grant_json"])
            request = action.plan.request
            # Reconciliation checks scope without demanding another budget unit.
            if (
                not admit(grant, owner, run.repository_id, run.source_commit).allowed
                or request.phase != "BASELINE"
                or request.run_id != run.run_id
                or request.repository_id != run.repository_id
                or request.source_commit != run.source_commit
                or request.source_manifest_ref != run.source_manifest_ref
                or request.reproduction_test_ref != run.reproduction_test_ref
                or any(
                    not allowed_entry(grant, p, "100644") for p in request.test_paths
                )
            ):
                raise StoreError("BASELINE_BINDING_DENIED")
            prior = conn.execute(
                "SELECT action_digest,from_version,to_version "
                "FROM baseline_dispatch_intents "
                "WHERE execution_id=%s",
                (execution_id,),
            ).fetchone()
            if prior is not None:
                if (
                    prior["action_digest"] != row["digest"]
                    or expected_version != prior["from_version"]
                ):
                    raise StoreError("BASELINE_CONFLICT")
                return int(str(prior["to_version"]))
            if expected_version != run.version:
                raise StoreError("STALE_VERSION")
            if (
                conn.execute(
                    "SELECT execution_id FROM baseline_receipts WHERE execution_id=%s",
                    (execution_id,),
                ).fetchone()
                is not None
            ):
                raise StoreError("BASELINE_ALREADY_COMPLETED")
            data = run.model_dump()
            data.update(
                remaining_budget=debit_baseline(
                    self._store, conn, run, action.plan
                ).model_dump(),
                version=run.version + 1,
            )
            updated = RunState.model_validate(data)
            conn.execute(
                "INSERT INTO baseline_dispatch_intents "
                "(execution_id,action_digest,from_version,to_version,reserved_seconds) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    execution_id,
                    row["digest"],
                    run.version,
                    updated.version,
                    action.plan.timeout_seconds,
                ),
            )
            conn.execute(
                "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
                (Jsonb(updated.model_dump(mode="json")), updated.version, run.run_id),
            )
            self._store._event(conn, updated, "BUDGET_RESERVED")
            return updated.version

    def retain(
        self,
        lease: Lease,
        owner: UUID,
        execution_id: UUID,
        signed: SignedReceipt,
        junit: bytes,
        verifier: ReceiptVerifier,
        *,
        expected_version: int,
    ) -> str:
        lease = Lease.model_validate(lease.model_dump())
        signed = SignedReceipt.model_validate(signed.model_dump())
        with self._store._connection() as conn:
            run = self._fence(conn, lease, owner, expected_version)
            row = conn.execute(
                """SELECT payload,digest,clock_timestamp() AS now FROM baseline_actions
                WHERE execution_id=%s AND run_id=%s AND expires_at>clock_timestamp()""",
                (execution_id, run.run_id),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            action = _decode(row["payload"], row["digest"], BaselineAction)
            # Recheck current grant/checkpoint before any receipt retention.
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if consent is None:
                raise StoreError("BASELINE_SCOPE_DENIED")
            grant = RepositoryGrant.model_validate(consent["grant_json"])
            request = action.plan.request
            # Completion evidence must survive the last permitted command debit.
            # This path cannot schedule another command or refund any budget.
            if (
                not admit(grant, owner, run.repository_id, run.source_commit).allowed
                or request.phase != "BASELINE"
                or request.run_id != run.run_id
                or request.repository_id != run.repository_id
                or request.source_commit != run.source_commit
                or request.source_manifest_ref != run.source_manifest_ref
                or request.reproduction_test_ref != run.reproduction_test_ref
                or any(
                    not allowed_entry(grant, p, "100644") for p in request.test_paths
                )
            ):
                raise StoreError("BASELINE_BINDING_DENIED")
            # Clock comes only from PostgreSQL, never a model/caller.
            accepted_at = row["now"]
            if not isinstance(accepted_at, datetime):
                raise StoreError("BASELINE_CLOCK")
            result = verifier.inspect_baseline(
                signed,
                action.plan,
                junit,
                execution_id=execution_id,
                source_tree_ref=action.source_tree_ref,
                expected=action.expected,
                now=accepted_at,
            )
            existing = conn.execute(
                "SELECT payload,digest FROM baseline_receipts WHERE execution_id=%s",
                (execution_id,),
            ).fetchone()
            if existing is not None:
                retained = _decode(
                    existing["payload"], existing["digest"], RetainedReceipt
                )
                if canonical(retained.signed) != canonical(signed):
                    raise StoreError("BASELINE_CONFLICT")
                return "sha256:" + str(existing["digest"])
            body = _payload(
                RetainedReceipt(signed=signed, result=result, accepted_at=accepted_at)
            )
            digest = hashlib.sha256(body).hexdigest()
            conn.execute(
                """INSERT INTO baseline_receipts
                (execution_id,digest,payload,report_payload) VALUES (%s,%s,%s,%s)""",
                (execution_id, digest, body, junit),
            )
            conn.execute(
                """INSERT INTO baseline_action_events
                (execution_id,kind,run_version,trace_id,worker_id)
                VALUES (%s,'RECEIPT_RETAINED',%s,%s,%s)""",
                (execution_id, run.version, run.trace_id, lease.worker_id),
            )
        return "sha256:" + digest

    def report(self, execution_id: UUID, owner: UUID) -> bytes:
        """Read private bounded report bytes bound to the retained signed digest.

        Producers must redact before signing. No public route or event exposes
        these bytes. Historical receipts without report bytes cannot be replayed
        through this path; neither signature nor parsing checks are bypassed.
        """
        with self._store._connection() as conn:
            row = conn.execute(
                """SELECT s.payload,s.digest,s.report_payload FROM baseline_receipts s
                JOIN baseline_actions a ON a.execution_id=s.execution_id
                JOIN runs r ON r.id=a.run_id WHERE s.execution_id=%s AND r.owner_id=%s
                AND s.expires_at>clock_timestamp()
                AND a.expires_at>clock_timestamp()""",
                (execution_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        retained = _decode(row["payload"], row["digest"], RetainedReceipt)
        body = row["report_payload"]
        if body is None:
            raise StoreError("BASELINE_REPORT_UNAVAILABLE")
        if not isinstance(body, (bytes, bytearray, memoryview)):
            raise StoreError("BASELINE_REPORT_INTEGRITY")
        report = bytes(body)
        if (
            not 1 <= len(report) <= 131072
            or retained.signed.payload.execution_id != execution_id
            or retained.signed.payload.junit_ref
            != "sha256:" + hashlib.sha256(report).hexdigest()
        ):
            raise StoreError("BASELINE_REPORT_INTEGRITY")
        return report

    def receipt(self, execution_id: UUID, owner: UUID) -> RetainedReceipt:
        with self._store._connection() as conn:
            row = conn.execute(
                """SELECT s.payload,s.digest FROM baseline_receipts s
                JOIN baseline_actions a ON a.execution_id=s.execution_id
                JOIN runs r ON r.id=a.run_id WHERE s.execution_id=%s AND r.owner_id=%s
                AND s.expires_at>clock_timestamp()
                AND a.expires_at>clock_timestamp()""",
                (execution_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        retained = _decode(row["payload"], row["digest"], RetainedReceipt)
        if retained.signed.payload.execution_id != execution_id:
            raise StoreError("BASELINE_INTEGRITY")
        return retained
