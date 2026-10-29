"""Sticky replay pair reservations; no submission or execution authority."""

import hashlib
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit, allowed_entry
from services.persistence.baseline import BaselineJournal
from services.persistence.model_budget import debit_replays, reservation_transaction
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import canonical
from services.verifier.replay import ReplayActions, canonical_actions


def _decode(payload: object, digest: object) -> ReplayActions:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise StoreError("REPLAY_RESERVATION_INTEGRITY")
    body = bytes(payload)
    if not 1 <= len(body) <= 32768 or hashlib.sha256(body).hexdigest() != digest:
        raise StoreError("REPLAY_RESERVATION_INTEGRITY")
    try:
        actions = ReplayActions.model_validate_json(body)
        if canonical_actions(actions) != body:
            raise StoreError("REPLAY_RESERVATION_INTEGRITY")
        return actions
    except (ValidationError, ValueError):
        raise StoreError("REPLAY_RESERVATION_INTEGRITY") from None


class ReplayJournal:
    """One immutable pair per run; duplicate reads never authorize resubmission."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def load(self, run_id: UUID, owner: UUID) -> ReplayActions:
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT a.payload,a.digest FROM replay_reservations a "
                "JOIN runs r ON r.id=a.run_id WHERE a.run_id=%s AND r.owner_id=%s "
                "AND a.expires_at>clock_timestamp()",
                (run_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("REPLAY_RESERVATION_REQUIRED")
        actions = _decode(row["payload"], row["digest"])
        if actions.candidate_plan.request.run_id != run_id:
            raise StoreError("REPLAY_RESERVATION_INTEGRITY")
        return actions

    def operation_id(
        self,
        run_id: UUID,
        owner: UUID,
        phase: Literal["CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"],
    ) -> UUID:
        """Return only the known remote operation bound to the retained plan."""
        if phase not in {"CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"}:
            raise StoreError("REPLAY_PHASE_INVALID")
        actions = self.load(run_id, owner)
        plan = {
            "CANDIDATE": actions.candidate_plan,
            "EXISTING_SUITE": actions.suite_plan,
            "CLEAN_REPLAY": actions.clean_plan,
        }[phase]
        if plan is None:
            raise StoreError("REPLAY_SUITE_ACTION_REQUIRED")
        from services.runner.receipts import plan_ref

        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT s.plan_ref,s.origin,r.operation_id FROM replay_submissions s "
                "LEFT JOIN replay_submission_results r USING(run_id,phase) "
                "JOIN runs u ON u.id=s.run_id WHERE s.run_id=%s AND u.owner_id=%s "
                "AND s.phase=%s",
                (run_id, owner, phase),
            ).fetchone()
        if (
            row is None
            or row["plan_ref"] != plan_ref(plan)
            or row["origin"] != "REMOTE_SERVICE"
            or row["operation_id"] is None
        ):
            raise StoreError("REPLAY_KNOWN_OPERATION_REQUIRED")
        try:
            return UUID(str(row["operation_id"]))
        except (TypeError, ValueError):
            raise StoreError("REPLAY_OPERATION_INTEGRITY") from None

    def reserve(
        self,
        lease: Lease,
        owner: UUID,
        actions: ReplayActions,
        *,
        expected_version: int,
    ) -> RunState:
        """Reserve two commands and their full timeouts before any provider I/O.

        This records intent, not permission to dispatch. Unknown submission stays
        sticky; there is no retry/refund/reset operation. Runner admission and
        actual operation submission remain separate trusted controller actions.
        """
        lease = Lease.model_validate(lease.model_dump())
        actions = ReplayActions.model_validate(actions.model_dump())
        body = canonical_actions(actions)
        if len(body) > 32768:
            raise StoreError("REPLAY_RESERVATION_LIMIT")
        if type(expected_version) is not int or expected_version < 0:
            raise StoreError("STALE_VERSION")
        baseline = BaselineJournal(self._store).receipt(
            actions.baseline_execution_id, owner
        )
        original = BaselineJournal(self._store).action(
            actions.baseline_execution_id, owner
        )
        with reservation_transaction(self._store) as conn:
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
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            before, after = actions.candidate_plan.request, actions.clean_plan.request
            if (
                run.current_stage != Stage.VERIFYING
                or run.cancellation_requested
                or not run.patch_attempt_refs
                or before.phase != "CANDIDATE"
                or actions.candidate_plan.command_id != actions.clean_plan.command_id
                or after.phase != "CLEAN_REPLAY"
                or before.model_dump(exclude={"phase"})
                != after.model_dump(exclude={"phase"})
                or before.run_id != run.run_id
                or before.repository_id != run.repository_id
                or before.source_commit != run.source_commit
                or before.source_manifest_ref != run.source_manifest_ref
                or before.reproduction_test_ref != run.reproduction_test_ref
                or before.patch_ref != run.patch_attempt_refs[-1]
                or baseline.signed.payload.run_id != run.run_id
                or baseline.signed.payload.origin != "REMOTE_SERVICE"
                or baseline.result.status != "MATCHED_SIGNED_REPORT"
                or run.baseline_result_ref
                != "sha256:" + hashlib.sha256(canonical(baseline)).hexdigest()
                or original.expected.test_name != actions.test_name
                or original.plan.request.reproduction_path != before.reproduction_path
                or original.plan.request.reproduction_test_ref
                != before.reproduction_test_ref
            ):
                raise StoreError("REPLAY_RESERVATION_BINDING_DENIED")
            if actions.suite_plan is not None:
                suite = actions.suite_plan
                if (
                    actions.suite_execution_id is None
                    or suite.request.phase != "EXISTING_SUITE"
                    or suite.request.run_id != run.run_id
                    or suite.request.repository_id != run.repository_id
                    or suite.request.source_commit != run.source_commit
                    or suite.request.source_manifest_ref != run.source_manifest_ref
                    or suite.request.reproduction_test_ref != run.reproduction_test_ref
                    or suite.request.patch_ref != run.patch_attempt_refs[-1]
                    or suite.command_id != actions.candidate_plan.command_id
                    or suite.request.model_dump(exclude={"phase", "test_paths"})
                    != before.model_dump(exclude={"phase", "test_paths"})
                ):
                    raise StoreError("REPLAY_SUITE_BINDING_DENIED")
            if consent is None:
                raise StoreError("REPLAY_SCOPE_DENIED")
            grant = RepositoryGrant.model_validate(consent["grant_json"])
            if not admit(
                grant, owner, run.repository_id, run.source_commit
            ).allowed or any(
                not allowed_entry(grant, p, "100644")
                for p in (
                    *before.test_paths,
                    *(
                        actions.suite_plan.request.test_paths
                        if actions.suite_plan is not None
                        else ()
                    ),
                )
            ):
                raise StoreError("REPLAY_SCOPE_DENIED")
            prior = conn.execute(
                "SELECT payload,digest,from_version,to_version,"
                "expires_at>clock_timestamp() AS active FROM replay_reservations "
                "WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
            if prior is not None:
                saved = _decode(prior["payload"], prior["digest"])
                if (
                    not prior["active"]
                    or saved != actions
                    or prior["from_version"] != expected_version
                ):
                    raise StoreError("REPLAY_RESERVATION_CONFLICT")
                return run
            if run.version != expected_version or run.evidence_manifest_ref is not None:
                raise StoreError("STALE_VERSION")
            remaining = debit_replays(
                self._store,
                conn,
                run,
                actions.candidate_plan,
                actions.clean_plan,
                actions.suite_plan,
            )
            updated = RunState.model_validate(
                {
                    **run.model_dump(),
                    "version": run.version + 1,
                    "remaining_budget": remaining.model_dump(),
                }
            )
            conn.execute(
                "INSERT INTO replay_reservations"
                "(run_id,digest,payload,from_version,to_version) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    run.run_id,
                    hashlib.sha256(body).hexdigest(),
                    body,
                    run.version,
                    updated.version,
                ),
            )
            conn.execute(
                "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
                (Jsonb(updated.model_dump(mode="json")), updated.version, run.run_id),
            )
            self._store._event(conn, updated, "BUDGET_RESERVED")
            return updated
