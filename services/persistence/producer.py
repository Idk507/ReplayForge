"""Fenced private producer-state journal; no provider calls or signing keys."""

from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, TypeVar
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from packages.contracts.execution import TestCommandPlan
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit, allowed_entry
from services.persistence.store import Lease, Store, StoreError
from services.runner.prepared import PreparedImageBinding
from services.runner.producer import ProducedReceipt
from services.runner.receipts import SignedReplayReceipt, canonical, plan_ref
from services.runner.workspace import WorkspaceMeasurement

T = TypeVar("T", bound=BaseModel)


class Preparation(BaseModel):
    """Exact measured source bound to the immutable dispatch inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    binding: PreparedImageBinding
    plan: TestCommandPlan
    source: WorkspaceMeasurement
    observed_at: AwareDatetime


def _decode(row: dict[str, Any], model: type[T], limit: int) -> T:
    raw = row["payload"]
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise StoreError("PRODUCER_STATE_INTEGRITY")
    body = bytes(raw)
    if not row["active"]:
        raise StoreError("PRODUCER_STATE_EXPIRED")
    if not 1 <= len(body) <= limit or sha256(body).hexdigest() != row["digest"]:
        raise StoreError("PRODUCER_STATE_INTEGRITY")
    try:
        value = model.model_validate_json(body)
        if canonical(value) != body:
            raise StoreError("PRODUCER_STATE_INTEGRITY")
        return value
    except (ValidationError, ValueError):
        raise StoreError("PRODUCER_STATE_INTEGRITY") from None


class ProducerJournal:
    """Trusted worker dependency, scoped to one reservation/phase/key registry."""

    def __init__(
        self,
        store: Store,
        lease: Lease,
        owner: UUID,
        phase: Literal["CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"],
        binding: PreparedImageBinding,
        *,
        receipt_key_id: str,
        receipt_public_key: Ed25519PublicKey,
        origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"],
        expected_version: int,
    ) -> None:
        if (
            phase not in {"CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"}
            or origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}
            or type(expected_version) is not int
            or not isinstance(receipt_public_key, Ed25519PublicKey)
        ):
            raise StoreError("PRODUCER_CONFIG_INVALID")
        self.store, self.lease, self.owner = (
            store,
            Lease.model_validate(lease.model_dump()),
            owner,
        )
        self.phase, self.binding = (
            phase,
            PreparedImageBinding.model_validate(binding.model_dump()),
        )
        self.key_id, self.key = receipt_key_id, receipt_public_key
        self.origin, self.version = origin, expected_version

    def _fence(
        self, conn: psycopg.Connection[dict[str, Any]]
    ) -> tuple[RunState, TestCommandPlan, UUID | None]:
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (self.lease.run_id, self.owner),
        ).fetchone()
        job = conn.execute(
            "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
            "AND token=%s AND status='leased' AND "
            "leased_until>clock_timestamp() FOR UPDATE",
            (self.lease.run_id, self.lease.worker_id, self.lease.token),
        ).fetchone()
        if row is None or job is None:
            raise StoreError("LEASE_LOST")
        run = RunState.model_validate(row["checkpoint"])
        if (
            run.version != self.version
            or run.current_stage != Stage.VERIFYING
            or run.cancellation_requested
        ):
            raise StoreError("PRODUCER_RUN_DENIED")
        # Load again under the fence rather than accepting caller-provided plans.
        reservation = conn.execute(
            "SELECT payload,digest FROM replay_reservations WHERE run_id=%s "
            "AND expires_at>clock_timestamp()",
            (run.run_id,),
        ).fetchone()
        if reservation is None:
            raise StoreError("REPLAY_RESERVATION_REQUIRED")
        from services.persistence.replay import _decode as decode_actions

        actions = decode_actions(reservation["payload"], reservation["digest"])
        plan = {
            "CANDIDATE": actions.candidate_plan,
            "EXISTING_SUITE": actions.suite_plan,
            "CLEAN_REPLAY": actions.clean_plan,
        }[self.phase]
        execution = {
            "CANDIDATE": actions.candidate_execution_id,
            "EXISTING_SUITE": actions.suite_execution_id,
            "CLEAN_REPLAY": actions.clean_execution_id,
        }[self.phase]
        if plan is None or execution is None:
            raise StoreError("PRODUCER_SUITE_ACTION_REQUIRED")
        request = plan.request
        if (
            request.run_id != run.run_id
            or request.repository_id != run.repository_id
            or request.source_commit != run.source_commit
            or request.source_manifest_ref != run.source_manifest_ref
            or request.reproduction_test_ref != run.reproduction_test_ref
            or not run.patch_attempt_refs
            or request.patch_ref != run.patch_attempt_refs[-1]
            or request.phase != self.phase
            or plan.command_id != "PYTEST_FILES_MEASURED_V1"
            or self.binding.plan_ref != plan_ref(plan)
            or self.binding.execution_id != execution
            or self.binding.tree_ref != actions.patched_tree_ref
            or self.binding.image_digest != request.image_digest
        ):
            raise StoreError("PRODUCER_BINDING_DENIED")
        scope = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, self.owner),
        ).fetchone()
        if scope is None:
            raise StoreError("PRODUCER_SCOPE_DENIED")
        grant = RepositoryGrant.model_validate(scope["grant_json"])
        if not admit(
            grant, self.owner, run.repository_id, run.source_commit
        ).allowed or any(
            not allowed_entry(grant, p, "100644") for p in request.test_paths
        ):
            raise StoreError("PRODUCER_SCOPE_DENIED")
        submission = conn.execute(
            "SELECT s.plan_ref,s.origin,r.operation_id FROM replay_submissions "
            "s LEFT JOIN replay_submission_results r USING(run_id,phase) WHERE "
            "s.run_id=%s AND s.phase=%s",
            (run.run_id, self.phase),
        ).fetchone()
        if (
            submission is None
            or submission["plan_ref"] != self.binding.plan_ref
            or submission["origin"] != self.origin
        ):
            raise StoreError("PRODUCER_SUBMISSION_REQUIRED")
        operation = submission["operation_id"]
        return run, plan, None if operation is None else UUID(str(operation))

    def _preparation(
        self, conn: psycopg.Connection[dict[str, Any]], plan: TestCommandPlan
    ) -> Preparation | None:
        row = conn.execute(
            "SELECT payload,digest,expires_at>clock_timestamp() AS active FROM "
            "replay_preparations WHERE run_id=%s AND phase=%s",
            (self.lease.run_id, self.phase),
        ).fetchone()
        if row is None:
            return None
        value = _decode(row, Preparation, 32768)
        if (
            value.binding != self.binding
            or value.plan != plan
            or value.source.image_uuid != self.binding.image_uuid
            or value.source.tree_ref != self.binding.tree_ref
            or value.source.evidence_origin != self.origin
        ):
            raise StoreError("PRODUCER_PREPARATION_DENIED")
        return value

    def retain_preparation(self, source: WorkspaceMeasurement) -> None:
        source = WorkspaceMeasurement.model_validate(source.model_dump())
        with self.store._connection() as conn:
            run, plan, operation = self._fence(conn)
            if operation is not None or self._preparation(conn, plan) is not None:
                raise StoreError("PRODUCER_ALREADY_PREPARED")
            if (
                source.image_uuid != self.binding.image_uuid
                or source.tree_ref != self.binding.tree_ref
                or source.evidence_origin != self.origin
            ):
                raise StoreError("PRODUCER_PREPARATION_DENIED")
            clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
            if clock is None or not isinstance(clock["now"], datetime):
                raise StoreError("PRODUCER_CLOCK_INVALID")
            body = canonical(
                Preparation(
                    binding=self.binding,
                    plan=plan,
                    source=source,
                    observed_at=clock["now"],
                )
            )
            inserted = conn.execute(
                "INSERT INTO "
                "replay_preparations(run_id,phase,digest,payload,expires_at) "
                "SELECT run_id,%s,%s,%s,expires_at FROM replay_reservations WHERE "
                "run_id=%s AND expires_at>clock_timestamp()",
                (self.phase, sha256(body).hexdigest(), body, run.run_id),
            )
            if inserted.rowcount != 1:
                raise StoreError("REPLAY_RESERVATION_REQUIRED")
            self.store._event(conn, run, "REPLAY_PREPARATION_RETAINED")

    def guard_submission(self) -> None:
        """No cached preparation can authorize another POST, even with unknown ID."""
        with self.store._connection() as conn:
            _, plan, operation = self._fence(conn)
            if operation is not None or self._preparation(conn, plan) is not None:
                raise StoreError("PRODUCER_ALREADY_PREPARED")

    def _check_receipt(
        self, result: ProducedReceipt, preparation: Preparation, operation: UUID
    ) -> None:
        result = ProducedReceipt.model_validate(result.model_dump())
        signed, payload = result.signed, result.signed.payload
        if (
            not isinstance(signed, SignedReplayReceipt)
            or signed.key_id != self.key_id
            or result.operation_id != operation
            or result.source != preparation.source
            or result.final.image_uuid == result.source.image_uuid
            or result.final.evidence_origin != self.origin
            or payload.origin != self.origin
            or payload.execution_id != self.binding.execution_id
            or payload.run_id != self.lease.run_id
            or payload.plan_ref != self.binding.plan_ref
            or payload.source_tree_ref != result.source.tree_ref
            or payload.final_tree_ref != result.final.tree_ref
            or payload.junit_ref != "sha256:" + sha256(result.junit).hexdigest()
            or not payload.terminal
        ):
            raise StoreError("PRODUCER_RECEIPT_DENIED")
        try:
            self.key.verify(
                bytes.fromhex(signed.signature), canonical(signed, unsigned=True)
            )
        except (InvalidSignature, ValueError):
            raise StoreError("PRODUCER_RECEIPT_SIGNATURE") from None

    def restore(
        self, operation: UUID
    ) -> tuple[TestCommandPlan, WorkspaceMeasurement, ProducedReceipt | None]:
        with self.store._connection() as conn:
            _, plan, known = self._fence(conn)
            if known is None or known != operation:
                raise StoreError("PRODUCER_KNOWN_OPERATION_REQUIRED")
            preparation = self._preparation(conn, plan)
            if preparation is None:
                raise StoreError("PRODUCER_PREPARATION_REQUIRED")
            row = conn.execute(
                "SELECT payload,digest,expires_at>clock_timestamp() AS active FROM "
                "replay_produced_receipts WHERE run_id=%s AND phase=%s",
                (self.lease.run_id, self.phase),
            ).fetchone()
            result = None if row is None else _decode(row, ProducedReceipt, 100000)
            if result is not None:
                self._check_receipt(result, preparation, known)
            return plan, preparation.source, result

    def retain_receipt(self, result: ProducedReceipt) -> ProducedReceipt:
        with self.store._connection() as conn:
            run, plan, known = self._fence(conn)
            preparation = self._preparation(conn, plan)
            if known is None or preparation is None:
                raise StoreError("PRODUCER_PREPARATION_REQUIRED")
            self._check_receipt(result, preparation, known)
            row = conn.execute(
                "SELECT payload,digest,expires_at>clock_timestamp() AS active FROM "
                "replay_produced_receipts WHERE run_id=%s AND phase=%s",
                (run.run_id, self.phase),
            ).fetchone()
            if row is not None:
                cached = _decode(row, ProducedReceipt, 100000)
                self._check_receipt(cached, preparation, known)
                # Read times may differ; immutable terminal facts must agree.
                if (
                    result.signed.payload.model_dump(exclude={"observed_at"})
                    != cached.signed.payload.model_dump(exclude={"observed_at"})
                    or result.final != cached.final
                    or result.junit != cached.junit
                ):
                    raise StoreError("PRODUCER_RECEIPT_CONFLICT")
                return cached
            body = canonical(result)
            if len(body) > 100000:
                raise StoreError("PRODUCER_RECEIPT_LIMIT")
            inserted = conn.execute(
                "INSERT INTO "
                "replay_produced_receipts(run_id,phase,digest,payload,expires_at) "
                "SELECT run_id,%s,%s,%s,expires_at FROM replay_preparations WHERE "
                "run_id=%s AND phase=%s AND expires_at>clock_timestamp()",
                (self.phase, sha256(body).hexdigest(), body, run.run_id, self.phase),
            )
            if inserted.rowcount != 1:
                raise StoreError("PRODUCER_STATE_EXPIRED")
            self.store._event(conn, run, "REPLAY_RECEIPT_RETAINED")
            return result

    def at_version(self, version: int) -> "ProducerJournal":
        """Rebind a read to the current CAS version; all DB fences still apply."""
        return ProducerJournal(
            self.store,
            self.lease,
            self.owner,
            self.phase,
            self.binding,
            receipt_key_id=self.key_id,
            receipt_public_key=self.key,
            origin=self.origin,
            expected_version=version,
        )

    def completed(self) -> ProducedReceipt:
        """Resolve the durable operation ID internally; never query the provider."""
        with self.store._connection() as conn:
            _, _, operation = self._fence(conn)
        if operation is None:
            raise StoreError("PRODUCER_KNOWN_OPERATION_REQUIRED")
        _, _, result = self.restore(operation)
        if result is None:
            raise StoreError("PRODUCER_RECEIPT_REQUIRED")
        return result
