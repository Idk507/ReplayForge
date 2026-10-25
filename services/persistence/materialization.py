"""Durable source-upload intent, operation and unsigned measured result custody."""

from hashlib import sha256
from typing import Literal, TypeVar
from uuid import UUID

import psycopg
from pydantic import BaseModel, ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit, allowed_entry
from services.persistence.model_budget import (
    debit_materialization,
    reservation_transaction,
)
from services.persistence.store import Lease, Store, StoreError
from services.runner.materialization import MaterializationPlan, MaterializationResult
from services.runner.receipts import canonical

T = TypeVar("T", bound=BaseModel)


def _decode(raw: object, digest: object, model: type[T]) -> T:
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise StoreError("MATERIALIZATION_INTEGRITY")
    data = bytes(raw)
    if len(data) > 65536 or sha256(data).hexdigest() != digest:
        raise StoreError("MATERIALIZATION_INTEGRITY")
    try:
        result = model.model_validate_json(data)
        if canonical(result) != data:
            raise StoreError("MATERIALIZATION_INTEGRITY")
        return result
    except ValidationError:
        raise StoreError("MATERIALIZATION_INTEGRITY") from None


class MaterializationJournal:
    """One original-workspace upload per run; runtime receives SELECT/INSERT only.

    Unknown intent is sticky even if no POST happened. Expiry, source/test
    bindings and lease/version/current consent are never renewed on recovery.
    A result is measured private metadata, never attested runner identity.
    """

    def __init__(
        self, store: Store, *, origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    ) -> None:
        if origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}:
            raise StoreError("MATERIALIZATION_ORIGIN_DENIED")
        self.store, self.origin = store, origin

    def _fence(
        self,
        conn: psycopg.Connection[dict[str, object]],
        lease: Lease,
        owner: UUID,
        plan: MaterializationPlan,
        version: int,
    ) -> RunState:
        if type(version) is not int or plan.run_id != lease.run_id:
            raise StoreError("MATERIALIZATION_BINDING_DENIED")
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
        if (
            run.version != version
            or run.current_stage != Stage.REPRODUCING
            or run.cancellation_requested
            or run.repository_id != plan.repository_id
            or run.source_commit != plan.source_commit
            or run.source_manifest_ref != plan.source_manifest_ref
            or run.reproduction_test_ref != plan.reproduction_test_ref
        ):
            raise StoreError("MATERIALIZATION_BINDING_DENIED")
        scope = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        if scope is None:
            raise StoreError("MATERIALIZATION_SCOPE_DENIED")
        grant = RepositoryGrant.model_validate(scope["grant_json"])
        if not admit(grant, owner, run.repository_id, run.source_commit).allowed or any(
            not allowed_entry(grant, path, "100644") for path in plan.paths
        ):
            raise StoreError("MATERIALIZATION_SCOPE_DENIED")
        return run

    def _intent(
        self, conn: psycopg.Connection[dict[str, object]], plan: MaterializationPlan
    ) -> int:
        row = conn.execute(
            "SELECT payload,digest,origin,from_version,expires_at>clock_timestamp() "
            "AS active FROM materialization_intents WHERE run_id=%s",
            (plan.run_id,),
        ).fetchone()
        if (
            row is None
            or not row["active"]
            or row["origin"] != self.origin
            or type(row["from_version"]) is not int
            or _decode(row["payload"], row["digest"], MaterializationPlan) != plan
        ):
            raise StoreError("MATERIALIZATION_INTENT_DENIED")
        return int(str(row["from_version"]))

    def claim(
        self,
        lease: Lease,
        owner: UUID,
        plan: MaterializationPlan,
        *,
        expected_version: int,
    ) -> bool:
        """Commit intent+budget+audit before POST; false is never a retry permit."""
        lease = Lease.model_validate(lease.model_dump())
        plan = MaterializationPlan.model_validate(plan.model_dump())
        if type(expected_version) is not int:
            raise StoreError("MATERIALIZATION_BINDING_DENIED")
        data = canonical(plan)
        if len(data) > 65536:
            raise StoreError("MATERIALIZATION_LIMIT")
        with reservation_transaction(self.store) as conn:
            # Run lock serializes competing claims before reading the ledger.
            conn.execute("SELECT id FROM runs WHERE id=%s FOR UPDATE", (lease.run_id,))
            prior = conn.execute(
                "SELECT run_id FROM materialization_intents WHERE run_id=%s",
                (plan.run_id,),
            ).fetchone()
            if prior is not None:
                original = self._intent(conn, plan)
                self._fence(conn, lease, owner, plan, original + 1)
                if expected_version not in {original, original + 1}:
                    raise StoreError("MATERIALIZATION_INTENT_DENIED")
                return False
            run = self._fence(conn, lease, owner, plan, expected_version)
            remaining = debit_materialization(self.store, conn, run)
            conn.execute(
                "INSERT INTO materialization_intents"
                "(run_id,payload,digest,from_version,origin) "
                "VALUES(%s,%s,%s,%s,%s)",
                (plan.run_id, data, sha256(data).hexdigest(), run.version, self.origin),
            )
            updated = run.model_copy(
                update={"version": run.version + 1, "remaining_budget": remaining}
            )
            self.store._save(conn, updated)
            self.store._event(conn, updated, "MATERIALIZATION_INTENT_RECORDED")
            return True

    def authorize(self, lease: Lease, owner: UUID, plan: MaterializationPlan) -> None:
        with self.store._connection() as conn:
            original = self._intent(conn, plan)
            self._fence(conn, lease, owner, plan, original + 1)

    def operation(
        self,
        lease: Lease,
        owner: UUID,
        plan: MaterializationPlan,
        operation_id: UUID | None = None,
    ) -> UUID | None:
        """Bind once or retrieve the already-owned provider ID, under fresh fences."""
        with self.store._connection() as conn:
            original = self._intent(conn, plan)
            run = self._fence(conn, lease, owner, plan, original + 1)
            row = conn.execute(
                "SELECT operation_id FROM materialization_operations WHERE run_id=%s",
                (plan.run_id,),
            ).fetchone()
            if row is not None:
                known = row["operation_id"]
                if not isinstance(known, UUID) or (
                    operation_id is not None and known != operation_id
                ):
                    raise StoreError("MATERIALIZATION_OPERATION_DENIED")
                return known
            if operation_id is not None:
                if not isinstance(operation_id, UUID):
                    raise StoreError("MATERIALIZATION_OPERATION_DENIED")
                conn.execute(
                    "INSERT INTO materialization_operations(run_id,operation_id) "
                    "VALUES(%s,%s)",
                    (plan.run_id, operation_id),
                )
                self.store._event(conn, run, "MATERIALIZATION_OPERATION_KNOWN")
            return operation_id

    def result(
        self,
        lease: Lease,
        owner: UUID,
        plan: MaterializationPlan,
        observed: MaterializationResult | None = None,
    ) -> MaterializationResult | None:
        """Retain before return; cache reads preserve actual original observation."""
        with self.store._connection() as conn:
            original = self._intent(conn, plan)
            run = self._fence(conn, lease, owner, plan, original + 1)
            row = conn.execute(
                "SELECT payload,digest FROM materialization_results WHERE run_id=%s",
                (plan.run_id,),
            ).fetchone()
            if row is not None:
                saved = _decode(row["payload"], row["digest"], MaterializationResult)
                known = conn.execute(
                    "SELECT operation_id FROM materialization_operations "
                    "WHERE run_id=%s",
                    (plan.run_id,),
                ).fetchone()
                if (
                    saved.plan != plan
                    or known is None
                    or known["operation_id"] != saved.operation_id
                    or saved.measurement.evidence_origin != self.origin
                    or saved.measurement.tree_ref != plan.workspace_ref
                    or saved.measurement.image_uuid != saved.result_image_uuid
                ):
                    raise StoreError("MATERIALIZATION_RESULT_DENIED")
                if observed is not None and saved.model_dump(
                    exclude={"observed_at"}
                ) != observed.model_dump(exclude={"observed_at"}):
                    raise StoreError("MATERIALIZATION_RESULT_DENIED")
                return saved
            if observed is None:
                return None
            observed = MaterializationResult.model_validate(observed.model_dump())
            known = conn.execute(
                "SELECT operation_id FROM materialization_operations WHERE run_id=%s",
                (plan.run_id,),
            ).fetchone()
            if (
                known is None
                or known["operation_id"] != observed.operation_id
                or observed.plan != plan
                or observed.measurement.evidence_origin != self.origin
                or observed.measurement.tree_ref != plan.workspace_ref
                or observed.measurement.image_uuid != observed.result_image_uuid
            ):
                raise StoreError("MATERIALIZATION_RESULT_DENIED")
            data = canonical(observed)
            conn.execute(
                "INSERT INTO materialization_results(run_id,payload,digest) "
                "VALUES(%s,%s,%s)",
                (plan.run_id, data, sha256(data).hexdigest()),
            )
            self.store._event(conn, run, "MATERIALIZATION_MEASURED")
            return observed
