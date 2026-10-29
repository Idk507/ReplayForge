"""Authenticated verdict reducer; internal trusted worker only, never a model tool."""

import hashlib
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from packages.contracts.run import RunState, Stage
from services.persistence.evidence import EvidenceStore
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import canonical
from services.verifier.authenticated import (
    AuthenticatedVerdict,
    GateContext,
    GateDenied,
    GatePayload,
    GatePhaseBinding,
    SignedGates,
)
from services.verifier.azure_es256 import AzureES256GateEnvelopeV5
from services.verifier.signing import ProductionGateSigner


class RetainedVerification(BaseModel):
    """Private audit record; deserializing this object grants no authority."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    signed: SignedGates | AzureES256GateEnvelopeV5
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
    persisted_envelope = row.get("envelope_bytes")
    persisted_version = row.get("envelope_version")
    if (
        isinstance(record.signed, AzureES256GateEnvelopeV5)
        and persisted_envelope is None
    ):
        raise StoreError("VERIFICATION_ENVELOPE_INTEGRITY")
    if persisted_envelope is not None:
        expected_version = 4 if isinstance(record.signed, SignedGates) else 5
        if (
            not isinstance(persisted_envelope, (bytes, bytearray, memoryview))
            or bytes(persisted_envelope) != canonical(record.signed)
            or persisted_version != expected_version
        ):
            raise StoreError("VERIFICATION_ENVELOPE_INTEGRITY")
    elif persisted_version is not None:
        raise StoreError("VERIFICATION_ENVELOPE_INTEGRITY")
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
        signed: SignedGates | AzureES256GateEnvelopeV5,
        verifier: Any,
        *,
        image_digest: str,
        configuration_ref: str,
        phase_bindings: tuple[GatePhaseBinding, ...],
        expected_version: int,
    ) -> RunState:
        """Reauthenticate actual retained bytes inside the committing transaction.

        Exact duplicate delivery after completion is read-only reconciliation;
        it cannot renew evidence, consume a lease, or reopen a completed stage.
        No caller-supplied verdict is accepted and no publication is authorized.
        """
        lease = Lease.model_validate(lease.model_dump())
        if isinstance(signed, SignedGates):
            signed = SignedGates.model_validate(signed.model_dump())
        elif isinstance(signed, AzureES256GateEnvelopeV5):
            signed = AzureES256GateEnvelopeV5.validate_profile(signed.model_dump())
        else:
            raise StoreError("VERIFICATION_ENVELOPE_PROFILE_DENIED")
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (lease.run_id, owner),
            ).fetchone()
            if row is None:
                raise StoreError("NOT_FOUND")
            run = RunState.model_validate(row["checkpoint"])
            previous = conn.execute(
                "SELECT payload,digest,envelope_version,envelope_bytes,"
                "expires_at>clock_timestamp() AS active "
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
                    or context.phase_bindings != phase_bindings
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
                phase_bindings=phase_bindings,
                expected_version=expected_version,
            )
            body = canonical(RetainedVerification(signed=signed, verdict=verdict))
            if len(body) > 32768:
                raise StoreError("VERIFICATION_LIMIT")
            digest = hashlib.sha256(body).hexdigest()
            conn.execute(
                "INSERT INTO verification_results("
                "run_id,digest,payload,envelope_version,envelope_bytes) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    run.run_id,
                    digest,
                    body,
                    4 if isinstance(signed, SignedGates) else 5,
                    canonical(signed),
                ),
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

    def sign_and_accept(
        self,
        lease: Lease,
        owner: UUID,
        payload: GatePayload,
        signer: object,
        verifier: Any,
        *,
        image_digest: str,
        configuration_ref: str,
        phase_bindings: tuple[GatePhaseBinding, ...],
        expected_version: int,
    ) -> RunState:
        """Sign and persist a trusted producer payload through the verifier.

        The caller must derive all records from durable evidence. The injected
        signer owns no key here; this method adds no key generation or evidence
        authority and uses the existing journal acceptance checks.
        """
        from services.verifier.azure_es256 import AzureES256GateSignerV5

        if not isinstance(signer, (ProductionGateSigner, AzureES256GateSignerV5)):
            raise StoreError("GATE_SIGNER_UNAVAILABLE")
        try:
            signed = signer.sign(payload)
        except Exception:
            raise StoreError("GATE_SIGNING_FAILED") from None
        return self.accept(
            lease,
            owner,
            signed,
            verifier,
            image_digest=image_digest,
            configuration_ref=configuration_ref,
            phase_bindings=phase_bindings,
            expected_version=expected_version,
        )

    def get(self, run_id: UUID, owner: UUID) -> RetainedVerification:
        """Owner-scoped integrity/expiry checked read; never reopens an action."""
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT v.payload,v.digest,v.envelope_version,v.envelope_bytes,"
                "r.checkpoint FROM verification_results v "
                "JOIN runs r ON r.id=v.run_id WHERE v.run_id=%s AND r.owner_id=%s "
                "AND v.expires_at>clock_timestamp()",
                (run_id, owner),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        return _decode(row, RunState.model_validate(row["checkpoint"]))

    def get_verified(
        self,
        run_id: UUID,
        owner: UUID,
        verifier: Any,
        *,
        image_digest: str,
        configuration_ref: str,
        phase_bindings: tuple[GatePhaseBinding, ...],
    ) -> RetainedVerification:
        """Independently revalidate persisted signature and retained artifact bytes.

        The verifier and runtime identities are injected by trusted application
        configuration. Serialized gate claims and the stored verdict are never
        used as the expected context.
        """
        record = self.get(run_id, owner)
        run = self._store.get(run_id, owner)
        if (
            run.current_stage
            not in {Stage.VERIFIED, Stage.NOT_VERIFIED, Stage.INCONCLUSIVE}
            or run.verification_ref is None
            or run.evidence_manifest_ref is None
            or not run.patch_attempt_refs
            or run.reproduction_test_ref is None
            or run.version != record.verdict.context.run_version + 1
        ):
            raise StoreError("VERIFICATION_INTEGRITY")
        manifest, artifacts = EvidenceStore(self._store).read(
            run_id, owner, run.evidence_manifest_ref
        )
        expected = GateContext(
            run_id=run.run_id,
            repository_id=run.repository_id,
            run_version=run.version - 1,
            source_commit=run.source_commit,
            patch_ref=run.patch_attempt_refs[-1],
            image_digest=image_digest,
            configuration_ref=configuration_ref,
            test_ref=run.reproduction_test_ref,
            workspace_sha256=manifest.workspace_sha256,
            manifest_ref=run.evidence_manifest_ref,
            phase_bindings=phase_bindings,
        )
        try:
            verdict = verifier.verify(
                record.signed,
                expected,
                manifest,
                artifacts,
                # Revalidate freshness at the persisted acceptance instant;
                # current retention expiry is checked independently by get().
                now=record.verdict.observed_at,
            )
        except GateDenied:
            raise StoreError("VERIFICATION_SIGNATURE_INVALID") from None
        if (
            verdict.status != record.verdict.status
            or verdict.missing != record.verdict.missing
            or verdict.failed != record.verdict.failed
            or verdict.attestation_ref != record.verdict.attestation_ref
            or verdict.manifest_ref != record.verdict.manifest_ref
            or verdict.context != record.verdict.context
        ):
            raise StoreError("VERIFICATION_REVALIDATION_CONFLICT")
        return record
