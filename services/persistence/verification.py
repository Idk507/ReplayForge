"""Authenticated verdict reducer; internal trusted worker only, never a model tool."""

import hashlib
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from packages.contracts.run import RunState, Stage
from services.persistence.evidence import EvidenceStore
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import canonical
from services.verifier.authenticated import (
    AuthenticatedVerdict,
    GateVerifier,
    SignedGates,
)


class RetainedVerification(BaseModel):
    """Private audit record; deserializing this object grants no authority."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    signed: SignedGates
    verdict: AuthenticatedVerdict


def _decode(row: dict[str, object], run: RunState) -> RetainedVerification:
    payload = row["payload"]
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise StoreError("VERIFICATION_INTEGRITY")
    body = bytes(payload)
    if not 1 <= len(body) <= 32768 or hashlib.sha256(body).hexdigest() != row["digest"]:
        raise StoreError("VERIFICATION_INTEGRITY")
    try:
        record = RetainedVerification.model_validate_json(body)
    except ValidationError:
        raise StoreError("VERIFICATION_INTEGRITY") from None
    context = record.verdict.context
    if (
        canonical(record) != body
        or context != record.signed.payload.context
        or context.run_id != run.run_id
        or context.repository_id != run.repository_id
        or context.source_commit != run.source_commit
        or not run.patch_attempt_refs
        or context.patch_ref != run.patch_attempt_refs[-1]
        or context.test_ref != run.reproduction_test_ref
        or context.manifest_ref != run.evidence_manifest_ref
        or record.verdict.manifest_ref != context.manifest_ref
        or record.verdict.attestation_ref
        != "sha256:" + hashlib.sha256(canonical(record.signed)).hexdigest()
        or run.verification_ref != "sha256:" + str(row["digest"])
        or run.version < context.run_version + 1
    ):
        raise StoreError("VERIFICATION_INTEGRITY")
    return record


class VerificationJournal:
    """Commit signature evaluation, private record, checkpoint, event and job fence.

    Explicit gate keys and image/configuration are trusted operator inputs. No
    registry is enabled by default. This adapter does not deploy/assess a producer
    or prove underlying observations independently of that trusted producer.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def accept(
        self,
        lease: Lease,
        owner: UUID,
        signed: SignedGates,
        verifier: GateVerifier,
        *,
        image_digest: str,
        configuration_ref: str,
        expected_version: int,
    ) -> RunState:
        """Reauthenticate actual retained bytes inside the committing transaction.

        Exact duplicate delivery after completion is read-only reconciliation;
        it cannot renew evidence, consume a lease, or reopen a completed stage.
        No caller-supplied verdict is accepted and no publication is authorized.
        """
        lease = Lease.model_validate(lease.model_dump())
        signed = SignedGates.model_validate(signed.model_dump())
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (lease.run_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            run = RunState.model_validate(row["checkpoint"])
            previous = conn.execute(
                "SELECT payload,digest,expires_at>clock_timestamp() AS active "
                "FROM verification_results WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
            if previous is not None:
                record = _decode(previous, run)
                context = record.verdict.context
                if not previous["active"]:
                    raise StoreError("VERIFICATION_EXPIRED")
                if (
                    type(expected_version) is not int
                    or context.run_version != expected_version
                    or run.version != expected_version + 1
                    or run.current_stage != Stage(record.verdict.status)
                    or run.cancellation_requested
                    or canonical(record.signed) != canonical(signed)
                    or context.image_digest != image_digest
                    or context.configuration_ref != configuration_ref
                ):
                    raise StoreError("VERIFICATION_CONFLICT")
                return run
            run, verdict = EvidenceStore(self._store)._evaluate(
                conn,
                lease,
                owner,
                signed,
                verifier,
                image_digest=image_digest,
                configuration_ref=configuration_ref,
                expected_version=expected_version,
            )
            body = canonical(RetainedVerification(signed=signed, verdict=verdict))
            if len(body) > 32768:
                raise StoreError("VERIFICATION_LIMIT")
            digest = hashlib.sha256(body).hexdigest()
            conn.execute(
                "INSERT INTO verification_results(run_id,digest,payload) "
                "VALUES (%s,%s,%s)",
                (run.run_id, digest, body),
            )
            # Only this authenticated boundary may produce VERIFIED. The generic
            # transition reducer still rejects it, as do all model/API tools.
            updated = RunState.model_validate(
                {
                    **run.model_dump(),
                    "current_stage": verdict.status,
                    "version": run.version + 1,
                    "verification_ref": "sha256:" + digest,
                }
            )
            self._store._save(conn, updated)
            closed = conn.execute(
                "UPDATE job_leases SET status='done',token=NULL WHERE run_id=%s "
                "AND worker_id=%s AND token=%s AND status='leased' "
                "AND leased_until>clock_timestamp()",
                (run.run_id, lease.worker_id, lease.token),
            )
            if closed.rowcount != 1:
                raise StoreError("LEASE_LOST")
            return updated

    def get(self, run_id: UUID, owner: UUID) -> RetainedVerification:
        """Owner-scoped integrity/expiry checked read; never reopens an action."""
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT v.payload,v.digest,r.checkpoint FROM verification_results v "
                "JOIN runs r ON r.id=v.run_id WHERE v.run_id=%s AND r.owner_id=%s "
                "AND v.expires_at>clock_timestamp()",
                (run_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        return _decode(row, RunState.model_validate(row["checkpoint"]))
