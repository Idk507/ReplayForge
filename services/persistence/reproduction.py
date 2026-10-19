"""Atomic candidate retention and reducer checkpoint; never dispatches code."""

import hashlib
import json
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from packages.contracts.inference import ModelDispatch
from packages.contracts.repair import ReproductionProposal
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit
from packages.policies.transitions import transition
from services.orchestrator.reproduction import (
    ReproductionArtifact,
    bind_reproduction,
    load_reproduction,
)
from services.persistence.model_budget import debit_model, reservation_transaction
from services.persistence.store import Lease, Store, StoreError


class ReproductionJournal:
    """Worker-only adapter with append-only artifacts and live lease fencing."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def reserve_inference(
        self,
        lease: Lease,
        owner: UUID,
        *,
        expected_version: int,
        dispatch: ModelDispatch | None = None,
    ) -> tuple[UUID, int]:
        """Persist uncertain intent and debit before a caller submits inference.

        A recovered intent is never a new dispatch permit. Even a crash before
        submission remains uncertain, sacrificing availability to avoid charges
        from blind retries. No raw prompt or credentials are retained here.
        """
        lease = Lease.model_validate(lease.model_dump())
        if dispatch is not None:
            dispatch = ModelDispatch.model_validate(dispatch.model_dump())
        with reservation_transaction(self._store) as conn:
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
            run = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or expected_version != run.version:
                raise StoreError("STALE_VERSION")
            if (
                run.current_stage != Stage.TRIAGING
                or run.cancellation_requested
                or run.reproduction_test_ref
                or not run.context_refs
                or run.source_manifest_ref is None
            ):
                raise StoreError("TRIAGE_STAGE_DENIED")
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if (
                consent is None
                or not admit(
                    RepositoryGrant.model_validate(consent["grant_json"]),
                    owner,
                    run.repository_id,
                    run.source_commit,
                ).allowed
            ):
                raise StoreError("TRIAGE_SCOPE_DENIED")
            prior = conn.execute(
                "SELECT run_id FROM triage_intents WHERE run_id=%s", (run.run_id,)
            ).fetchone()
            if prior is not None:
                raise StoreError("TRIAGE_SUBMISSION_UNCERTAIN")
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "source_commit": run.source_commit,
                        "source_manifest_ref": run.source_manifest_ref,
                        "context_refs": run.context_refs,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            data = run.model_dump()
            data.update(
                version=run.version + 1,
                remaining_budget=debit_model(
                    self._store, conn, run, dispatch
                ).model_dump(),
            )
            updated = RunState.model_validate(data)
            attempt = uuid4()
            conn.execute(
                "INSERT INTO triage_intents "
                "(run_id,attempt_id,context_digest,from_version,to_version,"
                "dispatch_json) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    run.run_id,
                    attempt,
                    fingerprint,
                    run.version,
                    updated.version,
                    Jsonb(dispatch.model_dump(mode="json")) if dispatch else None,
                ),
            )
            conn.execute(
                "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
                (Jsonb(updated.model_dump(mode="json")), updated.version, run.run_id),
            )
            self._store._event(conn, updated, "BUDGET_RESERVED")
            return attempt, updated.version

    def retain(
        self,
        lease: Lease,
        owner: UUID,
        proposal: ReproductionProposal,
        *,
        expected_version: int,
    ) -> RunState:
        """Commit candidate, reference, stage and event in a single transaction."""
        lease = Lease.model_validate(lease.model_dump())
        with self._store._connection() as conn:
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
            run = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or run.version != expected_version:
                raise StoreError("STALE_VERSION")
            if run.current_stage != Stage.TRIAGING or run.reproduction_test_ref:
                raise StoreError("REPRODUCTION_STAGE_DENIED")
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if consent is None:
                raise StoreError("REPRODUCTION_SCOPE_DENIED")
            artifact = bind_reproduction(
                proposal,
                run,
                RepositoryGrant.model_validate(consent["grant_json"]),
                owner,
            )
            payload = artifact.canonical_bytes()
            if len(payload) > 131072:
                raise StoreError("REPRODUCTION_LIMIT")
            conn.execute(
                "INSERT INTO reproduction_artifacts(run_id,digest,payload) "
                "VALUES (%s,%s,%s)",
                (run.run_id, artifact.artifact_ref()[7:], payload),
            )
            data = run.model_dump()
            data["reproduction_test_ref"] = artifact.artifact_ref()
            updated = transition(
                RunState.model_validate(data),
                Stage.REPRODUCING,
                expected_version=expected_version,
            )
            self._store._save(conn, updated)
            return updated

    def get(self, run_id: UUID, owner: UUID) -> ReproductionArtifact:
        """Read only an unexpired, integrity-checked candidate in current scope."""
        with self._store._connection() as conn:
            row = conn.execute(
                """SELECT a.payload,a.digest,r.checkpoint,g.grant_json
                FROM reproduction_artifacts a JOIN runs r ON r.id=a.run_id
                JOIN repository_grants g
                ON g.id=(r.checkpoint->>'repository_id')::uuid
                AND g.owner_id=r.owner_id WHERE a.run_id=%s AND r.owner_id=%s
                AND a.expires_at>clock_timestamp()""",
                (run_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        if not isinstance(row["payload"], (bytes, bytearray, memoryview)):
            raise StoreError("REPRODUCTION_INTEGRITY")
        return load_reproduction(
            bytes(row["payload"]),
            "sha256:" + str(row["digest"]),
            RunState.model_validate(row["checkpoint"]),
            RepositoryGrant.model_validate(row["grant_json"]),
            owner,
        )
