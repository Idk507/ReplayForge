"""Fenced custody for fresh candidate and clean patched workspaces."""

from hashlib import sha256
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, RunState, Stage
from packages.policies.repository import admit, allowed_entry
from services.persistence.materialization import _decode
from services.persistence.model_budget import (
    debit_materialization,
    reservation_transaction,
)
from services.persistence.store import Lease, Store, StoreError
from services.runner.materialization import MaterializationPlan, MaterializationResult
from services.runner.receipts import canonical


class PatchedMaterializationIntent(BaseModel):
    """One exact candidate/clean archive admission; never execution authority."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    phase: Literal["CANDIDATE", "CLEAN_REPLAY"]
    plan: MaterializationPlan
    patch_ref: ArtifactID


class PatchedMaterializationJournal:
    """Retain phase-specific upload POST intent before contacting a provider.

    A candidate and clean replay deliberately use two fresh workspaces. Neither
    operation substitutes for the original baseline materialization or grants a
    command execution permit.
    """

    def __init__(
        self,
        store: Store,
        phase: Literal["CANDIDATE", "CLEAN_REPLAY"],
        *,
        origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"],
    ) -> None:
        if phase not in {"CANDIDATE", "CLEAN_REPLAY"} or origin not in {
            "TEST_TRANSPORT",
            "REMOTE_SERVICE",
        }:
            raise StoreError("PATCH_WORKSPACE_CONFIG_DENIED")
        self.store, self.phase, self.origin = store, phase, origin

    def _intent(
        self, conn: psycopg.Connection[dict[str, object]], run_id: UUID
    ) -> tuple[PatchedMaterializationIntent, int]:
        row = conn.execute(
            "SELECT payload,digest,origin,from_version,expires_at>clock_timestamp() "
            "AS active FROM patched_materialization_intents "
            "WHERE run_id=%s AND phase=%s",
            (run_id, self.phase),
        ).fetchone()
        if (
            row is None
            or not row["active"]
            or row["origin"] != self.origin
            or type(row["from_version"]) is not int
        ):
            raise StoreError("PATCH_WORKSPACE_INTENT_DENIED")
        return (
            _decode(row["payload"], row["digest"], PatchedMaterializationIntent),
            int(row["from_version"]),
        )

    def _fence(
        self,
        conn: psycopg.Connection[dict[str, object]],
        lease: Lease,
        owner: UUID,
        intent: PatchedMaterializationIntent,
        version: int,
    ) -> RunState:
        if type(version) is not int or intent.plan.run_id != lease.run_id:
            raise StoreError("PATCH_WORKSPACE_BINDING_DENIED")
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (lease.run_id, owner),
        ).fetchone()
        job = conn.execute(
            "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s AND "
            "token=%s AND status='leased' AND leased_until>clock_timestamp() "
            "FOR UPDATE",
            (lease.run_id, lease.worker_id, lease.token),
        ).fetchone()
        if row is None or job is None:
            raise StoreError("LEASE_LOST")
        run = RunState.model_validate(row["checkpoint"])
        plan = intent.plan
        if (
            # The sibling phase may have committed its own durable intent after
            # this one. Its version bump is not a reason to lose a known POST;
            # stage, consent and the exact immutable patch binding still fence
            # every GET/result write.
            run.version < version
            or run.current_stage != Stage.VERIFYING
            or run.cancellation_requested
            or not run.patch_attempt_refs
            or intent.patch_ref != run.patch_attempt_refs[-1]
            or run.repository_id != plan.repository_id
            or run.source_commit != plan.source_commit
            or run.source_manifest_ref != plan.source_manifest_ref
            or run.reproduction_test_ref != plan.reproduction_test_ref
        ):
            raise StoreError("PATCH_WORKSPACE_BINDING_DENIED")
        scope = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        if scope is None:
            raise StoreError("PATCH_WORKSPACE_SCOPE_DENIED")
        grant = RepositoryGrant.model_validate(scope["grant_json"])
        if not admit(grant, owner, run.repository_id, run.source_commit).allowed or any(
            not allowed_entry(grant, path, "100644") for path in plan.paths
        ):
            raise StoreError("PATCH_WORKSPACE_SCOPE_DENIED")
        return run

    def claim(
        self,
        lease: Lease,
        owner: UUID,
        intent: PatchedMaterializationIntent,
        *,
        expected_version: int,
    ) -> bool:
        """Persist exact phase/patch intent and budget before a one-shot POST."""
        lease = Lease.model_validate(lease.model_dump())
        intent = PatchedMaterializationIntent.model_validate(intent.model_dump())
        if type(expected_version) is not int or intent.phase != self.phase:
            raise StoreError("PATCH_WORKSPACE_BINDING_DENIED")
        body = canonical(intent)
        if len(body) > 65536:
            raise StoreError("PATCH_WORKSPACE_LIMIT")
        with reservation_transaction(self.store) as conn:
            conn.execute("SELECT id FROM runs WHERE id=%s FOR UPDATE", (lease.run_id,))
            prior = conn.execute(
                "SELECT run_id FROM patched_materialization_intents "
                "WHERE run_id=%s AND phase=%s",
                (lease.run_id, self.phase),
            ).fetchone()
            if prior is not None:
                saved, from_version = self._intent(conn, lease.run_id)
                if saved != intent:
                    raise StoreError("PATCH_WORKSPACE_INTENT_DENIED")
                self._fence(conn, lease, owner, saved, from_version + 1)
                if expected_version not in {from_version, from_version + 1}:
                    raise StoreError("PATCH_WORKSPACE_INTENT_DENIED")
                return False
            run = self._fence(conn, lease, owner, intent, expected_version)
            remaining = debit_materialization(self.store, conn, run)
            conn.execute(
                "INSERT INTO patched_materialization_intents"
                "(run_id,phase,payload,digest,from_version,origin) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    lease.run_id,
                    self.phase,
                    body,
                    sha256(body).hexdigest(),
                    run.version,
                    self.origin,
                ),
            )
            updated = run.model_copy(
                update={"version": run.version + 1, "remaining_budget": remaining}
            )
            self.store._save(conn, updated)
            self.store._event(conn, updated, "PATCH_WORKSPACE_INTENT_RECORDED")
            return True

    def authorize(
        self, lease: Lease, owner: UUID, intent: PatchedMaterializationIntent
    ) -> None:
        with self.store._connection() as conn:
            saved, from_version = self._intent(conn, lease.run_id)
            if saved != intent:
                raise StoreError("PATCH_WORKSPACE_INTENT_DENIED")
            self._fence(conn, lease, owner, saved, from_version + 1)

    def operation(
        self,
        lease: Lease,
        owner: UUID,
        intent: PatchedMaterializationIntent,
        operation_id: UUID | None = None,
    ) -> UUID | None:
        with self.store._connection() as conn:
            self.authorize(lease, owner, intent)
            row = conn.execute(
                "SELECT operation_id FROM patched_materialization_operations "
                "WHERE run_id=%s AND phase=%s",
                (lease.run_id, self.phase),
            ).fetchone()
            if row is not None:
                known = row["operation_id"]
                if not isinstance(known, UUID) or (
                    operation_id is not None and known != operation_id
                ):
                    raise StoreError("PATCH_WORKSPACE_OPERATION_DENIED")
                return known
            if operation_id is not None:
                conn.execute(
                    "INSERT INTO patched_materialization_operations"
                    "(run_id,phase,operation_id) VALUES(%s,%s,%s)",
                    (lease.run_id, self.phase, operation_id),
                )
                run = self.store.get(lease.run_id, owner)
                self.store._event(conn, run, "PATCH_WORKSPACE_OPERATION_KNOWN")
            return operation_id

    def result(
        self,
        lease: Lease,
        owner: UUID,
        intent: PatchedMaterializationIntent,
        observed: MaterializationResult | None = None,
    ) -> MaterializationResult | None:
        with self.store._connection() as conn:
            self.authorize(lease, owner, intent)
            row = conn.execute(
                "SELECT payload,digest FROM patched_materialization_results "
                "WHERE run_id=%s AND phase=%s",
                (lease.run_id, self.phase),
            ).fetchone()
            if row is not None:
                saved = _decode(row["payload"], row["digest"], MaterializationResult)
                if (
                    saved.plan != intent.plan
                    or saved.measurement.evidence_origin != self.origin
                ):
                    raise StoreError("PATCH_WORKSPACE_RESULT_DENIED")
                return saved
            if observed is None:
                return None
            observed = MaterializationResult.model_validate(observed.model_dump())
            operation = self.operation(lease, owner, intent)
            if (
                operation is None
                or observed.plan != intent.plan
                or observed.operation_id != operation
                or observed.measurement.evidence_origin != self.origin
                or observed.measurement.tree_ref != intent.plan.workspace_ref
                or observed.measurement.image_uuid != observed.result_image_uuid
            ):
                raise StoreError("PATCH_WORKSPACE_RESULT_DENIED")
            body = canonical(observed)
            conn.execute(
                "INSERT INTO patched_materialization_results"
                "(run_id,phase,payload,digest) VALUES(%s,%s,%s,%s)",
                (lease.run_id, self.phase, body, sha256(body).hexdigest()),
            )
            run = self.store.get(lease.run_id, owner)
            self.store._event(conn, run, "PATCH_WORKSPACE_MEASURED")
            return observed
