"""One-shot patch proposal journal; no inference, patch application or execution."""

import hashlib
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, ValidationError

from packages.contracts.repair import PatchProposal
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.budget import consume
from packages.policies.repository import admit
from packages.policies.transitions import transition
from services.persistence.store import Lease, Store, StoreError
from services.runner.patches import PatchAdmission, admit_patch
from services.runner.receipts import canonical


class RetainedPatch(BaseModel):
    """Inert untrusted proposal plus the deterministic admission metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    proposal: PatchProposal
    admission: PatchAdmission


class PatchJournal:
    """Conservative one-attempt implementation: uncertain calls never resubmit."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @staticmethod
    def _locked(
        conn: psycopg.Connection[dict[str, object]],
        lease: Lease,
        owner: UUID,
        version: int,
    ) -> tuple[RunState, RepositoryGrant]:
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
        if type(version) is not int or version != run.version:
            raise StoreError("STALE_VERSION")
        if (
            run.current_stage != Stage.PATCHING
            or run.cancellation_requested
            or run.baseline_result_ref is None
            or run.reproduction_test_ref is None
            or run.source_manifest_ref is None
        ):
            raise StoreError("PATCH_STAGE_DENIED")
        consent = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        if consent is None:
            raise StoreError("PATCH_SCOPE_DENIED")
        grant = RepositoryGrant.model_validate(consent["grant_json"])
        if not admit(grant, owner, run.repository_id, run.source_commit).allowed:
            raise StoreError("PATCH_SCOPE_DENIED")
        return run, grant

    def reserve(
        self, lease: Lease, owner: UUID, *, expected_version: int
    ) -> tuple[UUID, int]:
        """Debit one model call and record sticky intent in the same transaction."""
        lease = Lease.model_validate(lease.model_dump())
        with self._store._connection() as conn:
            run, _ = self._locked(conn, lease, owner, expected_version)
            if (
                conn.execute(
                    "SELECT 1 FROM patch_intents WHERE run_id=%s", (run.run_id,)
                ).fetchone()
                is not None
            ):
                raise StoreError("PATCH_SUBMISSION_UNCERTAIN")
            if run.remaining_budget.patch_attempts < 1:
                raise StoreError("BUDGET_EXHAUSTED")
            data = run.model_dump()
            data.update(
                version=run.version + 1,
                remaining_budget=consume(
                    run.remaining_budget, "model_calls"
                ).model_dump(),
            )
            updated = RunState.model_validate(data)
            attempt = uuid4()
            conn.execute(
                "INSERT INTO patch_intents VALUES (%s,%s,%s)",
                (run.run_id, attempt, updated.version),
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
        proposal: PatchProposal,
        *,
        expected_version: int,
    ) -> RunState:
        """Re-admit the actual diff and atomically store it with VERIFYING state."""
        proposal = PatchProposal.model_validate(proposal.model_dump())
        lease = Lease.model_validate(lease.model_dump())
        with self._store._connection() as conn:
            run, grant = self._locked(conn, lease, owner, expected_version)
            intent = conn.execute(
                "SELECT reserved_version FROM patch_intents WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
            if intent is None or intent["reserved_version"] != run.version:
                raise StoreError("PATCH_RESERVATION_REQUIRED")
            admission = admit_patch(
                proposal.unified_diff,
                run,
                grant,
                owner,
                source_commit=run.source_commit,
                source_manifest_ref=run.source_manifest_ref,
                baseline_result_ref=run.baseline_result_ref,
                reproduction_test_ref=run.reproduction_test_ref,
            )
            if proposal.files_changed != admission.files_changed:
                raise StoreError("PATCH_FILE_CLAIM_MISMATCH")
            body = canonical(RetainedPatch(proposal=proposal, admission=admission))
            if len(body) > 3100000:
                raise StoreError("PATCH_LIMIT")
            conn.execute(
                "INSERT INTO patch_candidates(run_id,digest,payload) VALUES (%s,%s,%s)",
                (run.run_id, hashlib.sha256(body).hexdigest(), body),
            )
            data = run.model_dump()
            data.update(
                patch_attempt_refs=(*run.patch_attempt_refs, admission.patch_ref),
                remaining_budget=consume(
                    run.remaining_budget, "patch_attempts"
                ).model_dump(),
            )
            updated = transition(
                RunState.model_validate(data),
                Stage.VERIFYING,
                expected_version=expected_version,
            )
            self._store._save(conn, updated)
            return updated

    def get(self, run_id: UUID, owner: UUID) -> RetainedPatch:
        """Owner-scoped, expiring read; never authorizes application or replay."""
        with self._store._connection() as conn:
            row = conn.execute(
                """SELECT p.payload,p.digest,r.checkpoint FROM patch_candidates p
                JOIN runs r ON r.id=p.run_id WHERE p.run_id=%s AND r.owner_id=%s
                AND p.expires_at>clock_timestamp()""",
                (run_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        if not isinstance(row["payload"], (bytes, bytearray, memoryview)):
            raise StoreError("PATCH_INTEGRITY")
        body = bytes(row["payload"])
        if len(body) > 3100000 or hashlib.sha256(body).hexdigest() != row["digest"]:
            raise StoreError("PATCH_INTEGRITY")
        try:
            artifact = RetainedPatch.model_validate_json(body)
            run = RunState.model_validate(row["checkpoint"])
        except ValidationError:
            raise StoreError("PATCH_INTEGRITY") from None
        admission = artifact.admission
        if (
            canonical(artifact) != body
            or admission.run_id != run.run_id
            or admission.source_commit != run.source_commit
            or admission.source_manifest_ref != run.source_manifest_ref
            or admission.baseline_result_ref != run.baseline_result_ref
            or admission.reproduction_test_ref != run.reproduction_test_ref
            or admission.patch_ref not in run.patch_attempt_refs
            or admission.files_changed != artifact.proposal.files_changed
            or admission.patch_ref
            != "sha256:"
            + hashlib.sha256(artifact.proposal.unified_diff.encode()).hexdigest()
        ):
            raise StoreError("PATCH_INTEGRITY")
        return artifact
