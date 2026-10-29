"""Fenced known-operation report collection; no dispatch or signing authority."""

from hashlib import sha256
from typing import Any
from uuid import UUID

import httpx
import psycopg
from pydantic import ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit, allowed_entry
from services.orchestrator.replay_dispatch import Phase
from services.persistence.replay import ReplayJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.observations import ReplayObservation, read_replay_observation
from services.runner.receipts import canonical, plan_ref
from services.runner.remote import SandboxConfig


def _decode(row: dict[str, Any]) -> ReplayObservation:
    raw = row["payload"]
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise StoreError("REPLAY_OBSERVATION_INTEGRITY")
    body = bytes(raw)
    if not 1 <= len(body) <= 100000 or sha256(body).hexdigest() != row["digest"]:
        raise StoreError("REPLAY_OBSERVATION_INTEGRITY")
    try:
        observed = ReplayObservation.model_validate_json(body)
    except (ValidationError, ValueError):
        raise StoreError("REPLAY_OBSERVATION_INTEGRITY") from None
    if canonical(observed) != body or observed.status == "PENDING":
        raise StoreError("REPLAY_OBSERVATION_INTEGRITY")
    return observed


def collect_replay(
    store: Store,
    lease: Lease,
    owner: UUID,
    phase: Phase,
    config: SandboxConfig,
    *,
    expected_version: int,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> ReplayObservation:
    """Observe once, retain terminal evidence atomically and reconcile on restart.

    Only durable submission results supply operation IDs. Pending reads are not
    cached as terminal. No POST/DELETE, signing, verdict, refund or retry occurs.
    Operator config's image mapping remains independently assessed deployment
    input; collected provider UUIDs do not establish OCI/tree identity.
    """
    if (
        phase not in {"CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"}
        or type(expected_version) is not int
    ):
        raise StoreError("REPLAY_COLLECTION_INVALID")
    lease = Lease.model_validate(lease.model_dump())
    actions = ReplayJournal(store).load(lease.run_id, owner)
    plan = {
        "CANDIDATE": actions.candidate_plan,
        "EXISTING_SUITE": actions.suite_plan,
        "CLEAN_REPLAY": actions.clean_plan,
    }[phase]
    execution_id = {
        "CANDIDATE": actions.candidate_execution_id,
        "EXISTING_SUITE": actions.suite_execution_id,
        "CLEAN_REPLAY": actions.clean_execution_id,
    }[phase]
    if plan is None or execution_id is None:
        raise StoreError("REPLAY_SUITE_ACTION_REQUIRED")
    reference = plan_ref(plan)
    origin = "TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"

    def load(
        conn: psycopg.Connection[dict[str, Any]],
    ) -> tuple[RunState, UUID, str, ReplayObservation | None]:
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (lease.run_id, owner),
        ).fetchone()
        job = conn.execute(
            "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
            "AND token=%s AND status='leased' "
            "AND leased_until>clock_timestamp() FOR UPDATE",
            (lease.run_id, lease.worker_id, lease.token),
        ).fetchone()
        if row is None or job is None:
            raise StoreError("LEASE_LOST")
        run = RunState.model_validate(row["checkpoint"])
        request = plan.request
        if (
            run.version != expected_version
            or run.current_stage != Stage.VERIFYING
            or run.cancellation_requested
            or run.run_id != request.run_id
            or request.phase != phase
            or run.repository_id != request.repository_id
            or run.source_commit != request.source_commit
            or run.source_manifest_ref != request.source_manifest_ref
            or run.reproduction_test_ref != request.reproduction_test_ref
            or not run.patch_attempt_refs
            or run.patch_attempt_refs[-1] != request.patch_ref
        ):
            raise StoreError("REPLAY_COLLECTION_BINDING_DENIED")
        scope = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        if scope is None:
            raise StoreError("REPLAY_SCOPE_DENIED")
        grant = RepositoryGrant.model_validate(scope["grant_json"])
        if not admit(grant, owner, run.repository_id, run.source_commit).allowed or any(
            not allowed_entry(grant, path, "100644") for path in request.test_paths
        ):
            raise StoreError("REPLAY_SCOPE_DENIED")
        submission = conn.execute(
            "SELECT s.plan_ref,s.capability_ref,s.origin,r.operation_id "
            "FROM replay_submissions s LEFT JOIN replay_submission_results r "
            "USING(run_id,phase) WHERE s.run_id=%s AND s.phase=%s",
            (run.run_id, phase),
        ).fetchone()
        if (
            submission is None
            or submission["operation_id"] is None
            or submission["plan_ref"] != reference
            or submission["origin"] != origin
        ):
            raise StoreError("REPLAY_KNOWN_OPERATION_REQUIRED")
        operation = UUID(str(submission["operation_id"]))
        capability = str(submission["capability_ref"])
        prior = conn.execute(
            "SELECT payload,digest,expires_at>clock_timestamp() AS active "
            "FROM replay_observations WHERE run_id=%s AND phase=%s",
            (run.run_id, phase),
        ).fetchone()
        cached = None
        if prior is not None:
            if not prior["active"]:
                raise StoreError("REPLAY_OBSERVATION_EXPIRED")
            cached = _decode(prior)
            if (
                cached.run_id != run.run_id
                or cached.execution_id != execution_id
                or cached.operation_id != operation
                or cached.plan_ref != reference
                or cached.capability_ref != capability
                or cached.evidence_origin != origin
                or cached.image_uuid != config.image_uuid
            ):
                raise StoreError("REPLAY_OBSERVATION_BINDING_DENIED")
        return run, operation, capability, cached

    with store._connection() as conn:
        before, operation, capability, cached = load(conn)
        if cached is not None:
            return cached
    observed = read_replay_observation(
        config,
        plan,
        execution_id,
        operation,
        capability,
        allow_live=allow_live,
        transport=transport,
    )
    with store._connection() as conn:
        after, _, _, cached = load(conn)
        if after != before:
            raise StoreError("STALE_VERSION")
        if cached is not None:
            if observed.status == "PENDING":
                return cached
            # Two safe reads can race; differing observation times are expected.
            if observed.model_dump(exclude={"observed_at"}) != cached.model_dump(
                exclude={"observed_at"}
            ):
                raise StoreError("REPLAY_OBSERVATION_CONFLICT")
            return cached
        if observed.status != "PENDING":
            body = canonical(observed)
            if len(body) > 100000:
                raise StoreError("REPLAY_OBSERVATION_LIMIT")
            inserted = conn.execute(
                "INSERT INTO replay_observations"
                "(run_id,phase,digest,payload,expires_at) "
                "SELECT run_id,%s,%s,%s,expires_at FROM replay_reservations "
                "WHERE run_id=%s AND expires_at>clock_timestamp()",
                (phase, sha256(body).hexdigest(), body, lease.run_id),
            )
            if inserted.rowcount != 1:
                raise StoreError("REPLAY_RESERVATION_REQUIRED")
            store._event(conn, after, "REPLAY_OBSERVATION_RETAINED")
        return observed
