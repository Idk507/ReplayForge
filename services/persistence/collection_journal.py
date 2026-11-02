"""Durable collection dispatch intents and explicit operator reconciliation.

A worker commits an intent row before the broker request and a SENT marker
immediately before sending, so a missing marker proves no request was initiated.
Once SENT exists the outcome is uncertain until a COMPLETED/REJECTED marker or an
advanced run checkpoint proves otherwise. Nothing here sends or retries a request:
reconciliation only changes durable state, never automatically and never
silently, and always appends an audit row. Requeue after an uncertain send is
permitted solely because collection is a read of one immutable pinned commit
(``READ_ONLY_PINNED_COMMIT``) and the old lease is fenced out by the reset.
"""

import hashlib
import re
from collections.abc import Callable
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel, ConfigDict

from packages.contracts.run import RunState, Stage
from packages.observability.logs import log_event
from packages.policies.transitions import TERMINAL, transition
from services.persistence.store import Lease, Store, StoreError

PRE_COLLECTION = (Stage.CREATED, Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING)
_OPERATOR = re.compile(r"[A-Za-z0-9._@-]{3,64}")


class DispatchState(StrEnum):
    CLAIMED_NO_INTENT = "CLAIMED_NO_INTENT"
    INTENT_NOT_SENT = "INTENT_NOT_SENT"
    SENT_UNCERTAIN = "SENT_UNCERTAIN"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    NOT_RECONCILABLE = "NOT_RECONCILABLE"


class Observation(BaseModel):
    """Durable facts about one run's latest collection claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: UUID
    state: DispatchState
    job_status: str
    attempt: int
    lease_sha256: str | None
    operation_id: UUID | None
    stage: Stage
    run_version: int
    lease_expired: bool


class Reconciliation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    run_id: UUID
    action: Literal["REQUEUE", "FAIL", "BLOCKED"]
    observed_state: DispatchState
    job_status: str
    stage: Stage
    blocked_reason: str | None = None


DiagnosticCode = Literal[
    "WAITING_FOR_WORKER",
    "WORKER_NOT_CLAIMING",
    "COLLECTING",
    "NEEDS_RECONCILIATION",
    "JOB_EXHAUSTED",
    "JOB_STOPPED",
    "ADVANCED",
    "TERMINAL",
]
STALE_QUEUE_SECONDS = 120


class Diagnostic(BaseModel):
    """Owner-visible, read-only explanation of why a run is where it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: UUID
    stage: Stage
    code: DiagnosticCode
    job_status: str
    attempt: int
    age_seconds: int
    lease_seconds_left: int | None
    dispatch_state: DispatchState | None
    operator_action_required: bool


def lease_fingerprint(lease: Lease) -> str:
    """Stable generation identifier that never stores the bearer token."""
    return hashlib.sha256(
        f"{lease.run_id}:{lease.worker_id}:{lease.token}".encode()
    ).hexdigest()


class CollectionJournal:
    def __init__(self, store: Store) -> None:
        self.store = store

    def record_intent(self, lease: Lease, expected_version: int) -> UUID:
        """Fenced, once-per-claim intent; refuses a second dispatch for a claim."""
        lease = Lease.model_validate(lease.model_dump())
        operation_id = uuid4()
        with self.store._connection() as conn:
            row = conn.execute(
                """SELECT r.checkpoint,j.attempt_count FROM runs r
                JOIN job_leases j ON j.run_id=r.id WHERE r.id=%s AND j.worker_id=%s
                AND j.token=%s AND j.status='leased'
                AND j.leased_until>clock_timestamp() FOR UPDATE OF j""",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None:
                raise StoreError("LEASE_LOST")
            state = RunState.model_validate(row["checkpoint"])
            if state.version != expected_version or state.current_stage not in (
                PRE_COLLECTION
            ):
                raise StoreError("STALE_VERSION")
            inserted = conn.execute(
                """INSERT INTO collection_dispatches
                (operation_id,run_id,attempt,worker_id,lease_sha256,run_version,
                 side_effect) VALUES (%s,%s,%s,%s,%s,%s,'READ_ONLY_PINNED_COMMIT')
                ON CONFLICT (run_id,lease_sha256) DO NOTHING RETURNING operation_id""",
                (
                    operation_id,
                    lease.run_id,
                    row["attempt_count"],
                    lease.worker_id,
                    lease_fingerprint(lease),
                    state.version,
                ),
            ).fetchone()
            if inserted is None:
                raise StoreError("DISPATCH_ALREADY_RECORDED")
        return operation_id

    def mark_sent(self, lease: Lease, operation_id: UUID) -> None:
        """Commit SENT under a valid lease immediately before the request."""
        lease = Lease.model_validate(lease.model_dump())
        with self.store._connection() as conn:
            row = conn.execute(
                """SELECT 1 FROM collection_dispatches d JOIN job_leases j
                ON j.run_id=d.run_id WHERE d.operation_id=%s AND d.run_id=%s
                AND d.lease_sha256=%s AND j.worker_id=%s AND j.token=%s
                AND j.status='leased' AND j.leased_until>clock_timestamp()""",
                (
                    operation_id,
                    lease.run_id,
                    lease_fingerprint(lease),
                    lease.worker_id,
                    lease.token,
                ),
            ).fetchone()
            if row is None:
                raise StoreError("LEASE_LOST")
            conn.execute(
                "INSERT INTO collection_dispatch_events(operation_id,kind) "
                "VALUES (%s,'SENT') ON CONFLICT DO NOTHING",
                (operation_id,),
            )

    def mark_outcome(
        self, operation_id: UUID, kind: Literal["COMPLETED", "REJECTED"]
    ) -> None:
        """Record a response that proves the outcome; valid after lease expiry."""
        if kind not in {"COMPLETED", "REJECTED"}:
            raise StoreError("OUTCOME_INVALID")
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO collection_dispatch_events(operation_id,kind) "
                "VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (operation_id, kind),
            )

    @staticmethod
    def _observe(
        conn: psycopg.Connection[dict[str, object]],
        run_id: UUID,
        owner: UUID | None,
        lock: bool,
    ) -> Observation:
        suffix = " FOR UPDATE OF r,j" if lock else ""
        row = conn.execute(
            """SELECT r.checkpoint,j.status,j.token,j.worker_id,j.attempt_count,
            (j.leased_until IS NOT NULL AND j.leased_until<=clock_timestamp())
            AS expired FROM runs r JOIN job_leases j ON j.run_id=r.id
            WHERE r.id=%s AND (%s::uuid IS NULL OR r.owner_id=%s)"""
            + suffix,
            (run_id, owner, owner),
        ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        state = RunState.model_validate(row["checkpoint"])
        fingerprint = None
        dispatch = None
        events: set[str] = set()
        if row["token"] is not None and row["worker_id"] is not None:
            fingerprint = hashlib.sha256(
                f"{run_id}:{row['worker_id']}:{row['token']}".encode()
            ).hexdigest()
            dispatch = conn.execute(
                "SELECT operation_id FROM collection_dispatches "
                "WHERE run_id=%s AND lease_sha256=%s",
                (run_id, fingerprint),
            ).fetchone()
            if dispatch is not None:
                events = {
                    str(r["kind"])
                    for r in conn.execute(
                        "SELECT kind FROM collection_dispatch_events "
                        "WHERE operation_id=%s",
                        (dispatch["operation_id"],),
                    ).fetchall()
                }
        if state.current_stage not in PRE_COLLECTION:
            kind = (
                DispatchState.NOT_RECONCILABLE
                if state.current_stage in TERMINAL
                else DispatchState.COMPLETED
            )
        elif row["attempt_count"] == 0 or row["status"] != "leased":
            kind = DispatchState.NOT_RECONCILABLE
        elif dispatch is None:
            kind = DispatchState.CLAIMED_NO_INTENT
        elif "COMPLETED" in events:
            kind = DispatchState.COMPLETED
        elif "REJECTED" in events:
            kind = DispatchState.REJECTED
        elif "SENT" in events:
            kind = DispatchState.SENT_UNCERTAIN
        else:
            kind = DispatchState.INTENT_NOT_SENT
        return Observation(
            run_id=run_id,
            state=kind,
            job_status=str(row["status"]),
            attempt=int(str(row["attempt_count"])),
            lease_sha256=fingerprint,
            operation_id=None
            if dispatch is None
            else UUID(str(dispatch["operation_id"])),
            stage=state.current_stage,
            run_version=state.version,
            lease_expired=bool(row["expired"]),
        )

    def observe(self, run_id: UUID, owner: UUID | None = None) -> Observation:
        with self.store._connection() as conn:
            return self._observe(conn, run_id, owner, False)

    def diagnose(self, run_id: UUID, owner: UUID) -> Diagnostic:
        """Classify from durable facts only; never claims progress that is absent."""
        with self.store._connection() as conn:
            row = conn.execute(
                """SELECT r.checkpoint,j.status,j.attempt_count,
                EXTRACT(EPOCH FROM clock_timestamp()-r.created_at)::int AS age,
                EXTRACT(EPOCH FROM j.leased_until-clock_timestamp())::int AS left
                FROM runs r JOIN job_leases j ON j.run_id=r.id
                WHERE r.id=%s AND r.owner_id=%s""",
                (run_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            state = RunState.model_validate(row["checkpoint"])
            status = str(row["status"])
            attempt = int(str(row["attempt_count"]))
            age = max(0, int(str(row["age"])))
            left = None if row["left"] is None else int(str(row["left"]))
            dispatch: DispatchState | None = None
            if state.current_stage in PRE_COLLECTION and status == "leased":
                dispatch = self._observe(conn, run_id, owner, False).state
        if state.current_stage in TERMINAL:
            code: DiagnosticCode = "TERMINAL"
        elif state.current_stage not in PRE_COLLECTION:
            code = "ADVANCED"
        elif status == "exhausted":
            code = "JOB_EXHAUSTED"
        elif status == "done":
            code = "JOB_STOPPED"
        elif status == "queued":
            code = (
                "WORKER_NOT_CLAIMING"
                if age > STALE_QUEUE_SECONDS
                else "WAITING_FOR_WORKER"
            )
        elif left is not None and left > 0:
            code = "COLLECTING"
        else:
            code = "NEEDS_RECONCILIATION"
        return Diagnostic(
            run_id=run_id,
            stage=state.current_stage,
            code=code,
            job_status=status,
            attempt=attempt,
            age_seconds=age,
            lease_seconds_left=None if left is None else max(0, left),
            dispatch_state=dispatch,
            operator_action_required=code
            in {"NEEDS_RECONCILIATION", "JOB_EXHAUSTED", "JOB_STOPPED"},
        )

    def candidates(self) -> tuple[Observation, ...]:
        """Operator listing: expired claims awaiting explicit reconciliation."""
        with self.store._connection() as conn:
            ids = conn.execute(
                """SELECT r.id FROM runs r JOIN job_leases j ON j.run_id=r.id
                WHERE j.status='leased' AND j.leased_until<=clock_timestamp()
                AND j.attempt_count>=1
                AND r.checkpoint->>'current_stage'=ANY(%s) ORDER BY r.id LIMIT 100""",
                ([s.value for s in PRE_COLLECTION],),
            ).fetchall()
            return tuple(
                self._observe(conn, UUID(str(r["id"])), None, False) for r in ids
            )

    def reconcile(
        self,
        run_id: UUID,
        owner: UUID,
        action: Literal["REQUEUE", "FAIL"],
        *,
        operator: str,
        reason: str,
        authorize: Callable[[UUID, UUID], None],
        accept_read_only_replay: bool = False,
    ) -> Reconciliation:
        """Owner-scoped, transactional, audited decision; never sends a request.

        ``authorize`` must raise unless the owner's current repository consent and
        installation access are valid (called before a requeue is committed).
        """
        if action not in {"REQUEUE", "FAIL"}:
            raise StoreError("RECONCILE_ACTION_INVALID")
        if not isinstance(operator, str) or not _OPERATOR.fullmatch(operator):
            raise StoreError("OPERATOR_INVALID")
        if not isinstance(reason, str) or not 8 <= len(reason) <= 500:
            raise StoreError("REASON_INVALID")
        state = self.store.get(run_id, owner)
        if action == "REQUEUE":
            try:
                authorize(owner, state.repository_id)
            except Exception:
                self._audit_blocked(run_id, owner, operator, reason, "CONSENT_DENIED")
                raise StoreError("RECONCILIATION_BLOCKED") from None
        blocked: str | None = None
        with self.store._connection() as conn:
            obs = self._observe(conn, run_id, owner, True)
            current = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s", (run_id,)
            ).fetchone()
            if current is None:
                raise StoreError("NOT_FOUND")
            run_state = RunState.model_validate(current["checkpoint"])
            if not obs.lease_expired:
                blocked = "LEASE_ACTIVE"
            elif obs.state in {DispatchState.COMPLETED, DispatchState.NOT_RECONCILABLE}:
                blocked = obs.state.value
            elif run_state.cancellation_requested:
                blocked = "CANCELLATION_PENDING"
            elif action == "REQUEUE" and obs.state == DispatchState.SENT_UNCERTAIN:
                dispatch = conn.execute(
                    "SELECT run_version,side_effect FROM collection_dispatches "
                    "WHERE operation_id=%s",
                    (obs.operation_id,),
                ).fetchone()
                if not accept_read_only_replay:
                    blocked = "UNCERTAIN_SEND_REQUIRES_READ_ONLY_REPLAY_ACCEPTANCE"
                elif (
                    dispatch is None
                    or dispatch["side_effect"] != "READ_ONLY_PINNED_COMMIT"
                    or dispatch["run_version"] != run_state.version
                    or run_state.context_refs
                ):
                    blocked = "REPLAY_NOT_PROVEN_SAFE"
            if blocked is None:
                if action == "REQUEUE":
                    conn.execute(
                        """UPDATE job_leases SET status='queued',worker_id=NULL,
                        token=NULL,leased_until=NULL,attempt_count=0 WHERE run_id=%s""",
                        (run_id,),
                    )
                    job_status, stage = "queued", run_state.current_stage
                else:
                    data = transition(
                        run_state,
                        Stage.INCONCLUSIVE,
                        expected_version=run_state.version,
                    ).model_dump()
                    data["failure_reason"] = "SOURCE_UNAVAILABLE"
                    final = RunState.model_validate(data)
                    Store._save(conn, final)
                    Store._event(conn, final, "STAGE_CHANGED")
                    conn.execute(
                        "UPDATE job_leases SET status='done',token=NULL "
                        "WHERE run_id=%s",
                        (run_id,),
                    )
                    job_status, stage = "done", final.current_stage
                audit_id = uuid4()
                conn.execute(
                    """INSERT INTO collection_reconciliations
                    (id,run_id,job_attempt,lease_sha256,operation_id,observed_state,
                     action,operator,reason,result_job_status,result_stage)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        audit_id,
                        run_id,
                        obs.attempt,
                        obs.lease_sha256,
                        obs.operation_id,
                        obs.state.value,
                        action,
                        operator,
                        reason,
                        job_status,
                        stage.value,
                    ),
                )
                log_event(
                    "reconcile.decision",
                    action=action,
                    status=job_status,
                    stage=stage,
                    run_id=run_id,
                    operator=operator,
                    attempt=obs.attempt,
                )
                return Reconciliation(
                    id=audit_id,
                    run_id=run_id,
                    action=action,
                    observed_state=obs.state,
                    job_status=job_status,
                    stage=stage,
                )
        self._audit_blocked(run_id, owner, operator, reason, blocked, obs)
        raise StoreError("RECONCILIATION_BLOCKED:" + blocked)

    def _audit_blocked(
        self,
        run_id: UUID,
        owner: UUID,
        operator: str,
        reason: str,
        why: str,
        obs: Observation | None = None,
    ) -> None:
        """Committed outside the refused transaction so denials stay visible."""
        obs = obs or self.observe(run_id, owner)
        log_event(
            "reconcile.blocked",
            level=30,
            run_id=run_id,
            operator=operator,
            status=obs.state.value,
            error_class=why[:48].replace("_", "_"),
            attempt=obs.attempt,
        )
        with self.store._connection() as conn:
            conn.execute(
                """INSERT INTO collection_reconciliations
                (id,run_id,job_attempt,lease_sha256,operation_id,observed_state,
                 action,operator,reason,result_job_status,result_stage)
                VALUES (%s,%s,%s,%s,%s,%s,'BLOCKED',%s,%s,%s,%s)""",
                (
                    uuid4(),
                    run_id,
                    obs.attempt,
                    obs.lease_sha256,
                    obs.operation_id,
                    obs.state.value,
                    operator,
                    (reason + " [" + why + "]")[:500],
                    obs.job_status,
                    obs.stage.value,
                ),
            )
