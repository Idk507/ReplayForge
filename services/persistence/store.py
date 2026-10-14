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
from pydantic import BaseModel, ConfigDict, ValidationError

from packages.contracts.context import ContextBundle
from packages.contracts.incident import IncidentReport
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.contracts.source import SealedSnapshot, SourceManifest
from packages.policies.budget import consume
from packages.policies.repository import admit, allowed_entry
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
    def _context_payload(
        bundle: ContextBundle, *, legacy_fields: tuple[str, ...] = ()
    ) -> bytes:
        """Validate bounded sanitized data, never accept raw logs or arbitrary blobs."""
        bundle = ContextBundle.model_validate(bundle.model_dump())
        if len({item.path for item in bundle.files}) != len(bundle.files):
            raise StoreError("CONTEXT_INTEGRITY")
        if any(
            hashlib.sha256(item.content.encode()).hexdigest() != item.sanitized_sha256
            for item in bundle.files
        ):
            raise StoreError("CONTEXT_INTEGRITY")
        data = bundle.model_dump(mode="json")
        for key in legacy_fields:
            # Read compatibility for artifacts written before REST acquisition.
            # Never used for new writes or to drop supplied provenance fields.
            data.pop(key)
        payload = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if len(payload) > 4096:
            raise StoreError("CONTEXT_LIMIT")
        return payload

    def store_context(
        self,
        lease: Lease,
        bundle: ContextBundle,
        *,
        expected_version: int,
        source_manifest: SourceManifest | None = None,
        sealed_snapshot: SealedSnapshot | None = None,
    ) -> RunState:
        """Commit sanitized context and TRIAGING checkpoint in one transaction.

        Trusted worker only: the collector must perform redaction before this
        call. No model/API tool can invoke it. Unknown commit outcomes require
        checkpoint reconciliation; a repeated stale write is not retried.
        """
        payload = self._context_payload(bundle)
        lease = Lease.model_validate(lease.model_dump())
        digest = hashlib.sha256(payload).hexdigest()
        source_payload = None
        if source_manifest is None:
            if bundle.evidence_origin != "NOT_RECORDED":
                raise StoreError("SOURCE_MANIFEST_REQUIRED")
        else:
            source_payload = self._source_payload(source_manifest)
            source_manifest = SourceManifest.model_validate(
                source_manifest.model_dump()
            )
            identities = {item.path: item.sha256 for item in source_manifest.files}
            if (
                source_manifest.source_commit != bundle.source_commit
                or source_manifest.evidence_origin != bundle.evidence_origin
                or source_manifest.commit_identity != bundle.commit_identity
                or any(identities.get(f.path) != f.source_sha256 for f in bundle.files)
            ):
                raise StoreError("SOURCE_MANIFEST_MISMATCH")
        sealed_payload = None
        if sealed_snapshot is not None:
            sealed_snapshot = SealedSnapshot.model_validate(
                sealed_snapshot.model_dump()
            )
            if (
                source_payload is None
                or sealed_snapshot.run_id != lease.run_id
                or (
                    sealed_snapshot.manifest_sha256
                    != hashlib.sha256(source_payload).hexdigest()
                )
            ):
                raise StoreError("SNAPSHOT_BINDING")
            sealed_payload = self._sealed_payload(sealed_snapshot)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint,owner_id FROM runs WHERE id=%s FOR UPDATE",
                (lease.run_id,),
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
            source_ref = None
            if source_manifest is not None and source_payload is not None:
                owner = UUID(str(row["owner_id"]))
                consent = conn.execute(
                    """SELECT grant_json FROM repository_grants
                    WHERE id=%s AND owner_id=%s""",
                    (state.repository_id, owner),
                ).fetchone()
                if (
                    consent is None
                    or source_manifest.repository_id != state.repository_id
                ):
                    raise StoreError("SOURCE_MANIFEST_SCOPE")
                grant = RepositoryGrant.model_validate(consent["grant_json"])
                if not admit(
                    grant, owner, state.repository_id, state.source_commit
                ).allowed or any(
                    not allowed_entry(grant, f.path, "100644")
                    for f in source_manifest.files
                ):
                    raise StoreError("SOURCE_MANIFEST_SCOPE")
                source_digest = hashlib.sha256(source_payload).hexdigest()
                conn.execute(
                    """INSERT INTO source_manifests(run_id,digest,payload)
                    VALUES (%s,%s,%s)""",
                    (state.run_id, source_digest, source_payload),
                )
                source_ref = "sha256:" + source_digest
            conn.execute(
                """INSERT INTO context_artifacts(run_id,digest,payload)
                VALUES (%s,%s,%s)""",
                (state.run_id, digest, payload),
            )
            data = state.model_dump()
            if sealed_payload is not None:
                sealed_digest = hashlib.sha256(sealed_payload).hexdigest()
                conn.execute(
                    """INSERT INTO sealed_snapshots(run_id,digest,payload)
                    VALUES (%s,%s,%s)""",
                    (state.run_id, sealed_digest, sealed_payload),
                )
                data["source_snapshot_ref"] = "sha256:" + sealed_digest
            data["context_refs"] = (*state.context_refs, f"sha256:{digest}")
            if source_ref is not None:
                data["source_manifest_ref"] = source_ref
            state = transition(
                RunState.model_validate(data),
                Stage.TRIAGING,
                expected_version=expected_version,
            )
            self._save(conn, state)
            # Collection hands off at TRIAGING; this collection job is finished.
            # Commit the fence with its artifact/checkpoint, not in a later call.
            conn.execute(
                "UPDATE job_leases SET status='done',token=NULL WHERE run_id=%s",
                (state.run_id,),
            )
            return state

    @staticmethod
    def _source_payload(manifest: SourceManifest) -> bytes:
        """Canonical bounded metadata; never accepts source contents or logs."""
        manifest = SourceManifest.model_validate(manifest.model_dump())
        payload = json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if len(payload) > 65536:
            raise StoreError("SOURCE_MANIFEST_LIMIT")
        return payload

    @staticmethod
    def _sealed_payload(sealed: SealedSnapshot) -> bytes:
        sealed = SealedSnapshot.model_validate(sealed.model_dump())
        payload = json.dumps(
            sealed.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if len(payload) > 4100000:
            raise StoreError("SNAPSHOT_LIMIT")
        return payload

    def sealed_snapshot(
        self, run_id: UUID, owner: UUID, artifact_id: str
    ) -> SealedSnapshot:
        """Trusted internal ciphertext read only; no public raw-source API route."""
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", artifact_id):
            raise StoreError("ARTIFACT_ID_INVALID")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT s.payload FROM sealed_snapshots s JOIN runs r ON r.id=s.run_id
                WHERE s.run_id=%s AND r.owner_id=%s AND s.digest=%s
                AND s.expires_at>clock_timestamp()""",
                (run_id, owner, artifact_id[7:]),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            payload = row["payload"]
            if (
                not isinstance(payload, bytes)
                or hashlib.sha256(payload).hexdigest() != artifact_id[7:]
            ):
                raise StoreError("SNAPSHOT_INTEGRITY")
            try:
                sealed = SealedSnapshot.model_validate_json(payload)
            except ValidationError:
                raise StoreError("SNAPSHOT_INTEGRITY") from None
            if sealed.run_id != run_id or self._sealed_payload(sealed) != payload:
                raise StoreError("SNAPSHOT_INTEGRITY")
            return sealed

    def source_manifest(
        self, run_id: UUID, owner: UUID, artifact_id: str
    ) -> SourceManifest:
        """Owner/retention/checksum/canonical read of a committed source manifest."""
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", artifact_id):
            raise StoreError("ARTIFACT_ID_INVALID")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT s.payload FROM source_manifests s JOIN runs r ON r.id=s.run_id
                WHERE s.run_id=%s AND r.owner_id=%s AND s.digest=%s
                AND s.expires_at>clock_timestamp()""",
                (run_id, owner, artifact_id[7:]),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            payload = row["payload"]
            if (
                not isinstance(payload, bytes)
                or hashlib.sha256(payload).hexdigest() != artifact_id[7:]
            ):
                raise StoreError("SOURCE_MANIFEST_INTEGRITY")
            try:
                manifest = SourceManifest.model_validate_json(payload)
            except ValidationError:
                raise StoreError("SOURCE_MANIFEST_INTEGRITY") from None
            if self._source_payload(manifest) != payload:
                raise StoreError("SOURCE_MANIFEST_INTEGRITY")
            return manifest

    def claimed_run(self, lease: Lease) -> tuple[UUID, RunState]:
        """Resolve owner/checkpoint only for an active trusted worker claim."""
        lease = Lease.model_validate(lease.model_dump())
        with self._connection() as conn:
            row = conn.execute(
                """SELECT r.owner_id,r.checkpoint FROM runs r
                JOIN job_leases j ON j.run_id=r.id WHERE r.id=%s
                AND j.worker_id=%s AND j.token=%s AND j.status='leased'
                AND j.leased_until>clock_timestamp()""",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None:
                raise StoreError("LEASE_LOST")
            return UUID(str(row["owner_id"])), RunState.model_validate(
                row["checkpoint"]
            )

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
            try:
                bundle = ContextBundle.model_validate_json(payload)
                stored = json.loads(payload)
                legacy_fields = tuple(
                    key
                    for key in ("commit_identity", "evidence_origin")
                    if key not in stored
                )
            except (ValidationError, ValueError):
                raise StoreError("ARTIFACT_INTEGRITY") from None
            if self._context_payload(bundle, legacy_fields=legacy_fields) != payload:
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
        self,
        state: RunState,
        owner: UUID,
        key: UUID,
        request_hash: str,
        *,
        incident: IncidentReport | None = None,
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
        incident_payload = None
        if incident is not None:
            incident = IncidentReport.model_validate(incident.model_dump())
            incident_payload = incident.model_dump_json().encode()
            if len(incident_payload) > 4096:
                raise StoreError("INCIDENT_LIMIT")
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
            if incident_payload is not None:
                conn.execute(
                    """INSERT INTO incident_reports(run_id,digest,payload)
                    VALUES (%s,%s,%s)""",
                    (
                        state.run_id,
                        hashlib.sha256(incident_payload).hexdigest(),
                        incident_payload,
                    ),
                )
            self._event(conn, state, "CREATED")
            conn.execute("INSERT INTO job_leases(run_id) VALUES (%s)", (state.run_id,))
        return state

    def incident(self, run_id: UUID, owner: UUID) -> IncidentReport:
        """Read retained immutable input with owner, digest and schema checks.

        Trusted internal consumer only; no public raw-report endpoint exists.
        Legacy metadata-only runs have no report and fail closed as NOT_FOUND.
        """
        with self._connection() as conn:
            row = conn.execute(
                """SELECT i.payload,i.digest FROM incident_reports i
                JOIN runs r ON r.id=i.run_id WHERE i.run_id=%s AND r.owner_id=%s
                AND i.expires_at>clock_timestamp()""",
                (run_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            payload = row["payload"]
            if not isinstance(payload, bytes) or (
                hashlib.sha256(payload).hexdigest() != row["digest"]
            ):
                raise StoreError("INCIDENT_INTEGRITY")
            try:
                report = IncidentReport.model_validate_json(payload)
            except ValidationError:
                raise StoreError("INCIDENT_INTEGRITY") from None
            if report.model_dump_json().encode() != payload:
                raise StoreError("INCIDENT_INTEGRITY")
            return report

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
                # Run/job locks fence cancellation and stale workers. Exhaustion
                # is an observed infrastructure limit, never a repair verdict.
                if state.current_stage not in TERMINAL:
                    state = RunState.model_validate(
                        {**state.model_dump(), "failure_reason": "LEASE_EXHAUSTED"}
                    )
                    state = transition(
                        state, Stage.INCONCLUSIVE, expected_version=state.version
                    )
                    self._save(conn, state)
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
        failure_reason: Literal[
            "SCOPE_DENIED",
            "SOURCE_MISMATCH",
            "CONTEXT_REJECTED",
            "SOURCE_REJECTED",
            "SOURCE_UNAVAILABLE",
        ]
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
