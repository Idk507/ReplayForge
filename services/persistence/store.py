"""Transactional PostgreSQL checkpoints, outbox events and fenced job leases."""

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from packages.contracts.context import ContextBundle
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.budget import consume
from packages.policies.transitions import TERMINAL, transition


class StoreError(Exception):
    """Safe code; database messages, SQL and credentials are never public."""


class Lease(BaseModel):
    """Bearer fence for one worker claim; not an API authorization token."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: UUID
    worker_id: UUID
    token: UUID
    leased_until: datetime


class Event(BaseModel):
    """Allowlisted metadata for replay, with no raw incident/source fields."""

    sequence: int
    event_type: str
    stage: Stage
    version: int
    trace_id: UUID
    observed_at: datetime


class EventPayload(BaseModel):
    """Validate the stored public subset before replay."""

    model_config = ConfigDict(extra="forbid")
    stage: Stage
    version: int


class Store:
    """Trusted internal adapter; caller identity must come from authentication.

    Fresh bounded connections avoid holding transactions between worker calls.
    No method performs external work or accepts a model-authored checkpoint write.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @staticmethod
    def _context_payload(bundle: ContextBundle) -> bytes:
        """Validate bounded sanitized data, never accept raw logs or arbitrary blobs."""
        bundle = ContextBundle.model_validate(bundle.model_dump())
        if len({item.path for item in bundle.files}) != len(bundle.files):
            raise StoreError("CONTEXT_INTEGRITY")
        if any(
            hashlib.sha256(item.content.encode()).hexdigest() != item.sanitized_sha256
            for item in bundle.files
        ):
            raise StoreError("CONTEXT_INTEGRITY")
        payload = json.dumps(
            bundle.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if len(payload) > 4096:
            raise StoreError("CONTEXT_LIMIT")
        return payload

    def store_context(
        self, lease: Lease, bundle: ContextBundle, *, expected_version: int
    ) -> RunState:
        """Commit sanitized context and TRIAGING checkpoint in one transaction.

        Trusted worker only: the collector must perform redaction before this
        call. No model/API tool can invoke it. Unknown commit outcomes require
        checkpoint reconciliation; a repeated stale write is not retried.
        """
        payload = self._context_payload(bundle)
        lease = Lease.model_validate(lease.model_dump())
        digest = hashlib.sha256(payload).hexdigest()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s FOR UPDATE", (lease.run_id,)
            ).fetchone()
            job = conn.execute(
                """SELECT run_id FROM job_leases WHERE run_id=%s
                AND worker_id=%s AND token=%s AND status='leased'
                AND leased_until>clock_timestamp() FOR UPDATE""",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None or job is None:
                raise StoreError("LEASE_LOST")
            state = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or state.version != expected_version:
                raise StoreError("STALE_VERSION")
            if state.current_stage != Stage.COLLECTING or state.cancellation_requested:
                raise StoreError("CONTEXT_STAGE_DENIED")
            if bundle.source_commit != state.source_commit:
                raise StoreError("CONTEXT_SOURCE_MISMATCH")
            conn.execute(
                """INSERT INTO context_artifacts(run_id,digest,payload)
                VALUES (%s,%s,%s)""",
                (state.run_id, digest, payload),
            )
            data = state.model_dump()
            data["context_refs"] = (*state.context_refs, f"sha256:{digest}")
            state = transition(
                RunState.model_validate(data),
                Stage.TRIAGING,
                expected_version=expected_version,
            )
            self._save(conn, state)
            return state

    def context(self, run_id: UUID, owner: UUID, artifact_id: str) -> ContextBundle:
        """Owner-scoped retained read with checksum and schema verification."""
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", artifact_id):
            raise StoreError("ARTIFACT_ID_INVALID")
        digest = artifact_id[7:]
        with self._connection() as conn:
            row = conn.execute(
                """SELECT a.payload FROM context_artifacts a
                JOIN runs r ON r.id=a.run_id WHERE a.run_id=%s AND r.owner_id=%s
                AND a.digest=%s AND a.expires_at>clock_timestamp()""",
                (run_id, owner, digest),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            payload = row["payload"]
            if (
                not isinstance(payload, bytes)
                or hashlib.sha256(payload).hexdigest() != digest
            ):
                raise StoreError("ARTIFACT_INTEGRITY")
            bundle = ContextBundle.model_validate_json(payload)
            if self._context_payload(bundle) != payload:
                raise StoreError("ARTIFACT_INTEGRITY")
            return bundle

    def register_repository(self, grant: RepositoryGrant) -> None:
        """Operator-only insertion; runtime must lack INSERT permission.

        Requires separately authenticated human consent. No public endpoint
        calls this method, and existing grants cannot be silently replaced.
        """
        grant = RepositoryGrant.model_validate(grant.model_dump())
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO repository_grants(id,owner_id,grant_json)
                VALUES (%s,%s,%s)""",
                (
                    grant.repository_id,
                    grant.owner_id,
                    Jsonb(grant.model_dump(mode="json")),
                ),
            )

    def repository(self, repository_id: UUID, owner: UUID) -> RepositoryGrant:
        """Resolve persisted consent without revealing another owner's grant."""
        with self._connection() as conn:
            row = conn.execute(
                """SELECT grant_json FROM repository_grants
                WHERE id=%s AND owner_id=%s""",
                (repository_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            grant = RepositoryGrant.model_validate(row["grant_json"])
            if grant.repository_id != repository_id or grant.owner_id != owner:
                raise StoreError("DB_INTEGRITY")
            return grant

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[dict[str, object]]]:
        try:
            with psycopg.connect(
                self.dsn,
                row_factory=dict_row,
                connect_timeout=5,
                options="-c statement_timeout=5000 -c lock_timeout=3000",
            ) as conn:
                yield conn
        except psycopg.Error as error:
            raise StoreError(f"DB_FAILURE_{error.sqlstate or 'UNAVAILABLE'}") from None

    @staticmethod
    def _event(
        conn: psycopg.Connection[dict[str, object]], state: RunState, kind: str
    ) -> None:
        conn.execute(
            """INSERT INTO run_events
            (run_id,sequence,event_type,public_payload,trace_id)
            VALUES (%s, (SELECT COALESCE(MAX(sequence),0)+1
                FROM run_events WHERE run_id=%s), %s,%s,%s)""",
            (
                state.run_id,
                state.run_id,
                kind,
                Jsonb({"stage": state.current_stage.value, "version": state.version}),
                state.trace_id,
            ),
        )

    def create(
        self, state: RunState, owner: UUID, key: UUID, request_hash: str
    ) -> RunState:
        """Commit initial checkpoint, event and job together; deduplicate by owner."""
        state = RunState.model_validate(state.model_dump())
        initial = RunState(
            run_id=state.run_id,
            incident_id=state.incident_id,
            repository_id=state.repository_id,
            source_commit=state.source_commit,
            policy_version=state.policy_version,
            trace_id=state.trace_id,
        )
        if state != initial:
            raise StoreError("INVALID_INITIAL_STATE")
        if not re.fullmatch(r"[a-f0-9]{64}", request_hash):
            raise StoreError("INVALID_REQUEST_HASH")
        with self._connection() as conn:
            # Serialize the same owner/key before INSERT/row-lock upgrades.
            # Unique indexes remain the durable backstop; hash collisions only
            # serialize unrelated requests, never conflate their identities.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{owner}:{key}",),
            )
            inserted = conn.execute(
                """INSERT INTO runs
                (id,owner_id,idempotency_key,request_hash,checkpoint,version)
                VALUES (%s,%s,%s,%s,%s,0) ON CONFLICT(owner_id,idempotency_key)
                DO NOTHING RETURNING id""",
                (
                    state.run_id,
                    owner,
                    key,
                    request_hash,
                    Jsonb(state.model_dump(mode="json")),
                ),
            ).fetchone()
            if inserted is None:
                row = conn.execute(
                    """SELECT request_hash,checkpoint FROM runs
                    WHERE owner_id=%s AND idempotency_key=%s FOR UPDATE""",
                    (owner, key),
                ).fetchone()
                if row is None:
                    raise StoreError("IDEMPOTENCY_CONFLICT")
                existing = RunState.model_validate(row["checkpoint"])
                identity = (
                    "repository_id",
                    "incident_id",
                    "source_commit",
                    "policy_version",
                )
                if row["request_hash"] != request_hash or any(
                    getattr(existing, f) != getattr(state, f) for f in identity
                ):
                    raise StoreError("IDEMPOTENCY_CONFLICT")
                return existing
            self._event(conn, state, "CREATED")
            conn.execute("INSERT INTO job_leases(run_id) VALUES (%s)", (state.run_id,))
        return state

    def get(self, run_id: UUID, owner: UUID) -> RunState:
        """Owner-scoped reads conceal another owner's existence."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s",
                (run_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            return RunState.model_validate(row["checkpoint"])

    def events(self, run_id: UUID, owner: UUID, after: int = 0) -> tuple[Event, ...]:
        """Bounded ordered outbox replay; caller continues from last sequence."""
        if type(after) is not int or after < 0:
            raise StoreError("INVALID_CURSOR")
        with self._connection() as conn:
            if (
                conn.execute(
                    "SELECT id FROM runs WHERE id=%s AND owner_id=%s", (run_id, owner)
                ).fetchone()
                is None
            ):
                raise StoreError("NOT_FOUND")
            rows = conn.execute(
                """SELECT sequence,event_type,public_payload,trace_id,created_at
                FROM run_events WHERE run_id=%s AND sequence>%s
                ORDER BY sequence LIMIT 100""",
                (run_id, after),
            ).fetchall()
            return tuple(
                Event.model_validate(
                    {
                        "sequence": r["sequence"],
                        "event_type": r["event_type"],
                        "trace_id": r["trace_id"],
                        "observed_at": r["created_at"],
                        **EventPayload.model_validate(r["public_payload"]).model_dump(),
                    }
                )
                for r in rows
            )

    @staticmethod
    def _save(conn: psycopg.Connection[dict[str, object]], state: RunState) -> None:
        conn.execute(
            "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
            (Jsonb(state.model_dump(mode="json")), state.version, state.run_id),
        )
        Store._event(
            conn,
            state,
            "CANCELLED" if state.current_stage == Stage.CANCELLED else "STAGE_CHANGED",
        )

    def cancel(self, run_id: UUID, owner: UUID, *, expected_version: int) -> RunState:
        """Atomically cancel metadata and fence the job; no sandbox exists yet."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (run_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            state = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or state.version != expected_version:
                raise StoreError("STALE_VERSION")
            state = transition(
                state, Stage.CANCELLED, expected_version=expected_version
            )
            self._save(conn, state)
            conn.execute(
                "UPDATE job_leases SET status='done',token=NULL WHERE run_id=%s",
                (run_id,),
            )
            return state

    def claim(self, worker: UUID) -> Lease | None:
        """Claim queued/expired work; at most three acquisitions, server clock."""
        with self._connection() as conn:
            row = conn.execute("""SELECT r.id, r.checkpoint FROM runs r
                JOIN job_leases j ON j.run_id=r.id WHERE
                (j.status='queued' OR (j.status='leased'
                    AND j.leased_until<=clock_timestamp()))
                ORDER BY r.id FOR UPDATE OF r,j SKIP LOCKED LIMIT 1""").fetchone()
            if row is None:
                return None
            job = conn.execute(
                """SELECT attempt_count FROM job_leases WHERE run_id=%s
                AND (status='queued' OR (status='leased'
                    AND leased_until<=clock_timestamp())) FOR UPDATE""",
                (row["id"],),
            ).fetchone()
            if job is None:
                raise StoreError("DB_INTEGRITY")
            state = RunState.model_validate(row["checkpoint"])
            if job["attempt_count"] == 3:
                conn.execute(
                    """UPDATE job_leases SET status='exhausted',token=NULL
                    WHERE run_id=%s""",
                    (row["id"],),
                )
                self._event(conn, state, "LEASE_EXHAUSTED")
                return None
            claimed = conn.execute(
                """UPDATE job_leases SET status='leased',worker_id=%s,token=%s,
                leased_until=clock_timestamp()+interval '30 seconds',
                attempt_count=attempt_count+1
                WHERE run_id=%s RETURNING run_id,worker_id,token,leased_until""",
                (worker, uuid4(), row["id"]),
            ).fetchone()
            self._event(conn, state, "LEASE_CLAIMED")
            return Lease.model_validate(claimed)

    def advance(
        self,
        lease: Lease,
        target: Stage,
        *,
        expected_version: int,
        failure_reason: Literal["SCOPE_DENIED", "SOURCE_MISMATCH", "CONTEXT_REJECTED"]
        | None = None,
    ) -> RunState:
        """Lock run then job; fence expired/superseded workers before committing."""
        lease = Lease.model_validate(lease.model_dump())
        with self._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s FOR UPDATE", (lease.run_id,)
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            job = conn.execute(
                """SELECT run_id FROM job_leases WHERE run_id=%s AND
                worker_id=%s AND token=%s AND status='leased'
                AND leased_until>clock_timestamp()
                FOR UPDATE""",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if job is None:
                raise StoreError("LEASE_LOST")
            state = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or state.version != expected_version:
                raise StoreError("STALE_VERSION")
            if failure_reason is not None:
                if target not in {Stage.POLICY_BLOCKED, Stage.INCONCLUSIVE}:
                    raise StoreError("INVALID_FAILURE_STAGE")
                state = RunState.model_validate(
                    {**state.model_dump(), "failure_reason": failure_reason}
                )
            state = transition(state, target, expected_version=expected_version)
            self._save(conn, state)
            if target in TERMINAL:
                conn.execute(
                    "UPDATE job_leases SET status='done',token=NULL WHERE run_id=%s",
                    (lease.run_id,),
                )
            return state

    def heartbeat(self, lease: Lease) -> Lease:
        """Renew only a still-live claim; an expired worker cannot revive itself."""
        lease = Lease.model_validate(lease.model_dump())
        with self._connection() as conn:
            row = conn.execute(
                """UPDATE job_leases SET
                leased_until=clock_timestamp()+interval '30 seconds'
                WHERE run_id=%s AND worker_id=%s AND token=%s
                AND status='leased' AND leased_until>clock_timestamp()
                RETURNING run_id,worker_id,token,leased_until""",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None:
                raise StoreError("LEASE_LOST")
            return Lease.model_validate(row)

    def reserve(
        self, lease: Lease, resource: str, *, expected_version: int
    ) -> RunState:
        """Persist an action debit and audit event before any caller dispatch.

        Unknown commit outcomes require reconciliation, never automatic refund
        or dispatch retry. Exactly-once external effects need their own ledger.
        """
        lease = Lease.model_validate(lease.model_dump())
        with self._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s FOR UPDATE", (lease.run_id,)
            ).fetchone()
            job = conn.execute(
                """SELECT run_id FROM job_leases WHERE run_id=%s
                AND worker_id=%s AND token=%s AND status='leased'
                AND leased_until>clock_timestamp() FOR UPDATE""",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None or job is None:
                raise StoreError("LEASE_LOST")
            state = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or expected_version != state.version:
                raise StoreError("STALE_VERSION")
            if state.current_stage in TERMINAL or state.cancellation_requested:
                raise StoreError("RUN_STOPPED")
            data = state.model_dump()
            data.update(
                remaining_budget=consume(state.remaining_budget, resource).model_dump(),
                version=state.version + 1,
            )
            updated = RunState.model_validate(data)
            conn.execute(
                "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
                (
                    Jsonb(updated.model_dump(mode="json")),
                    updated.version,
                    updated.run_id,
                ),
            )
            self._event(conn, updated, "BUDGET_RESERVED")
            return updated
