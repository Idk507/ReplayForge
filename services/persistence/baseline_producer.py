"""Fenced baseline premeasurement journal; no provider I/O or signing."""

import re
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from packages.contracts.execution import TestCommandPlan
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState
from packages.policies.repository import admit, allowed_entry
from services.persistence.baseline import BaselineAction, BaselineJournal, _decode
from services.persistence.operations import OperationJournal
from services.persistence.producer import Preparation
from services.persistence.producer import _decode as decode_producer
from services.persistence.store import Lease, Store, StoreError
from services.runner.prepared import PreparedImageBinding
from services.runner.producer import ProducedReceipt
from services.runner.receipts import SignedReceipt, canonical, plan_ref
from services.runner.workspace import WorkspaceMeasurement


class BaselineProducerJournal:
    """Retain actual preparation before POST and recover only a known operation.

    This slice does not sign, collect, dispatch, or authorize any execution.
    """

    def __init__(
        self,
        store: Store,
        lease: Lease,
        owner: UUID,
        binding: PreparedImageBinding,
        *,
        receipt_key_id: str,
        receipt_public_key: Ed25519PublicKey,
        origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"],
        expected_version: int,
    ) -> None:
        if (
            origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}
            or type(expected_version) is not int
            or not isinstance(receipt_key_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", receipt_key_id)
            or not isinstance(receipt_public_key, Ed25519PublicKey)
        ):
            raise StoreError("PRODUCER_CONFIG_INVALID")
        self.store, self.lease, self.owner = (
            store,
            Lease.model_validate(lease.model_dump()),
            owner,
        )
        self.binding = PreparedImageBinding.model_validate(binding.model_dump())
        self.origin, self.version = origin, expected_version
        self.key_id, self.key = receipt_key_id, receipt_public_key

    def _fence(
        self,
        conn: psycopg.Connection[dict[str, Any]],
    ) -> tuple[RunState, TestCommandPlan, UUID | None]:
        run = BaselineJournal._fence(conn, self.lease, self.owner, self.version)
        row = conn.execute(
            "SELECT a.payload,a.digest,s.plan_ref,s.runner_id,s.configuration_ref,"
            "s.origin,i.action_digest,i.to_version FROM baseline_actions a "
            "JOIN baseline_submissions s USING(execution_id) "
            "JOIN baseline_dispatch_intents i USING(execution_id) "
            "WHERE a.execution_id=%s AND a.run_id=%s "
            "AND a.expires_at>clock_timestamp() AND s.expires_at>clock_timestamp()",
            (self.binding.execution_id, run.run_id),
        ).fetchone()
        if row is None:
            raise StoreError("PRODUCER_SUBMISSION_REQUIRED")
        action = _decode(row["payload"], row["digest"], BaselineAction)
        plan, request, binding = action.plan, action.plan.request, self.binding
        if (
            action.execution_id != binding.execution_id
            or plan.command_id != "PYTEST_FILES_MEASURED_V1"
            or request.phase != "BASELINE"
            or request.run_id != run.run_id
            or request.repository_id != run.repository_id
            or request.source_commit != run.source_commit
            or request.source_manifest_ref != run.source_manifest_ref
            or request.reproduction_test_ref != run.reproduction_test_ref
            or binding.plan_ref != plan_ref(plan)
            or binding.tree_ref != action.source_tree_ref
            or binding.image_digest != request.image_digest
            or row["plan_ref"] != binding.plan_ref
            or row["runner_id"] != binding.runner_id
            or row["configuration_ref"] != binding.configuration_ref
            or row["origin"] != self.origin
            or row["action_digest"] != row["digest"]
            or row["to_version"] != run.version
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
            not allowed_entry(grant, path, "100644") for path in request.test_paths
        ):
            raise StoreError("PRODUCER_SCOPE_DENIED")
        history = OperationJournal._history(
            conn, binding.execution_id, run.run_id, str(row["digest"])
        )
        provider = (
            "TEST_TRANSPORT" if self.origin == "TEST_TRANSPORT" else "NEBIUS_CONTREE"
        )
        if not history or history[0].provider != provider:
            raise StoreError("PRODUCER_SUBMISSION_REQUIRED")
        return run, plan, history[1].operation_id if len(history) > 1 else None

    def _preparation(
        self,
        conn: psycopg.Connection[dict[str, Any]],
        plan: TestCommandPlan,
    ) -> Preparation | None:
        row = conn.execute(
            "SELECT payload,digest,expires_at>clock_timestamp() AS active "
            "FROM baseline_preparations WHERE execution_id=%s",
            (self.binding.execution_id,),
        ).fetchone()
        if row is None:
            return None
        value = decode_producer(row, Preparation, 32768)
        if (
            value.binding != self.binding
            or value.plan != plan
            or value.source.image_uuid != self.binding.image_uuid
            or value.source.tree_ref != self.binding.tree_ref
            or value.source.evidence_origin != self.origin
        ):
            raise StoreError("PRODUCER_PREPARATION_DENIED")
        return value

    def guard_submission(self) -> None:
        with self.store._connection() as conn:
            _, plan, known = self._fence(conn)
            if known is not None or self._preparation(conn, plan) is not None:
                raise StoreError("PRODUCER_ALREADY_PREPARED")

    def retain_preparation(self, source: WorkspaceMeasurement) -> None:
        source = WorkspaceMeasurement.model_validate(source.model_dump())
        with self.store._connection() as conn:
            run, plan, known = self._fence(conn)
            if known is not None or self._preparation(conn, plan) is not None:
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
                "INSERT INTO baseline_preparations"
                "(execution_id,digest,payload,expires_at) "
                "SELECT s.execution_id,%s,%s,LEAST(s.expires_at,a.expires_at) "
                "FROM baseline_submissions s JOIN baseline_actions a "
                "USING(execution_id) "
                "WHERE s.execution_id=%s AND s.expires_at>clock_timestamp() "
                "AND a.expires_at>clock_timestamp()",
                (sha256(body).hexdigest(), body, self.binding.execution_id),
            )
            if inserted.rowcount != 1:
                raise StoreError("PRODUCER_STATE_EXPIRED")
            conn.execute(
                "INSERT INTO baseline_action_events "
                "(execution_id,kind,run_version,trace_id,worker_id) "
                "VALUES (%s,'PREPARATION_RETAINED',%s,%s,%s)",
                (
                    self.binding.execution_id,
                    run.version,
                    run.trace_id,
                    self.lease.worker_id,
                ),
            )

    def restore_preparation(self, operation: UUID) -> Preparation:
        with self.store._connection() as conn:
            _, plan, known = self._fence(conn)
            if known is None or known != operation:
                raise StoreError("PRODUCER_KNOWN_OPERATION_REQUIRED")
            value = self._preparation(conn, plan)
            if value is None:
                raise StoreError("PRODUCER_PREPARATION_REQUIRED")
            return value

    def _check_receipt(
        self, result: ProducedReceipt, preparation: Preparation, operation: UUID
    ) -> None:
        result = ProducedReceipt.model_validate(result.model_dump())
        signed, payload = result.signed, result.signed.payload
        if (
            not isinstance(signed, SignedReceipt)
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
                "SELECT payload,digest,expires_at>clock_timestamp() AS active "
                "FROM baseline_produced_receipts WHERE execution_id=%s",
                (self.binding.execution_id,),
            ).fetchone()
            result = (
                None if row is None else decode_producer(row, ProducedReceipt, 100000)
            )
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
                "SELECT payload,digest,expires_at>clock_timestamp() AS active "
                "FROM baseline_produced_receipts WHERE execution_id=%s",
                (self.binding.execution_id,),
            ).fetchone()
            if row is not None:
                cached = decode_producer(row, ProducedReceipt, 100000)
                self._check_receipt(cached, preparation, known)
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
                "INSERT INTO baseline_produced_receipts"
                "(execution_id,digest,payload,expires_at) "
                "SELECT execution_id,%s,%s,expires_at FROM baseline_preparations "
                "WHERE execution_id=%s AND expires_at>clock_timestamp()",
                (sha256(body).hexdigest(), body, self.binding.execution_id),
            )
            if inserted.rowcount != 1:
                raise StoreError("PRODUCER_STATE_EXPIRED")
            conn.execute(
                "INSERT INTO baseline_action_events "
                "(execution_id,kind,run_version,trace_id,worker_id) "
                "VALUES (%s,'PRODUCED_RECEIPT_RETAINED',%s,%s,%s)",
                (
                    self.binding.execution_id,
                    run.version,
                    run.trace_id,
                    self.lease.worker_id,
                ),
            )
            return result

    def completed(self) -> ProducedReceipt:
        """Read the recorded operation and receipt without provider I/O."""
        with self.store._connection() as conn:
            _, _, operation = self._fence(conn)
        if operation is None:
            raise StoreError("PRODUCER_KNOWN_OPERATION_REQUIRED")
        _, _, result = self.restore(operation)
        if result is None:
            raise StoreError("PRODUCER_RECEIPT_REQUIRED")
        return result
