"""Authenticated gate evaluation over retained artifacts; no stage/PR mutation."""

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.run import ArtifactID, CommitSHA
from services.runner.receipts import canonical
from services.verifier.gates import GateEvidence, GateName, evaluate
from services.verifier.manifest import ArtifactInput, EvidenceManifest, verify_manifest


class GateDenied(Exception):
    """Stable denial without report bodies, key bytes or provider details."""


class ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class GateContext(ClosedModel):
    """Expected replay-input identities supplied only by trusted application code."""

    run_id: UUID
    repository_id: UUID
    run_version: int = Field(strict=True, ge=0)
    source_commit: CommitSHA
    patch_ref: ArtifactID
    image_digest: ArtifactID
    configuration_ref: ArtifactID
    test_ref: ArtifactID
    workspace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_ref: ArtifactID


class GatePayload(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    policy_version: Literal["mandatory-gates-v1"] = "mandatory-gates-v1"
    context: GateContext
    origin: Literal["REMOTE_SERVICE", "TEST_TRANSPORT"]
    observed_at: AwareDatetime
    gates: tuple[GateEvidence, ...] = Field(min_length=1, max_length=8)


class SignedGates(ClosedModel):
    purpose: Literal["replayforge.verification-gates.v1"] = (
        "replayforge.verification-gates.v1"
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    payload: GatePayload
    signature: str = Field(pattern=r"^[a-f0-9]{128}$", repr=False)


class AuthenticatedVerdict(ClosedModel):
    status: Literal["VERIFIED", "NOT_VERIFIED", "INCONCLUSIVE"]
    missing: tuple[GateName, ...]
    failed: tuple[GateName, ...]
    context: GateContext
    observed_at: AwareDatetime
    attestation_ref: ArtifactID
    manifest_ref: ArtifactID
    can_authorize_publication: Literal[False] = False


class GateVerifier:
    """Explicit public keys for trusted deterministic gate producers, not models.

    The producer must independently validate runner reports and local gate
    results before signing. This verifier authenticates those statements and
    binds actual artifact bytes. It neither deploys that producer nor assesses
    runner isolation, fetches reports, discovers keys or advances a run.
    """

    def __init__(self, keys: Mapping[str, Ed25519PublicKey]) -> None:
        if (
            not isinstance(keys, Mapping)
            or not 1 <= len(keys) <= 16
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)
                or not isinstance(key, Ed25519PublicKey)
                for name, key in keys.items()
            )
        ):
            raise GateDenied("GATE_CONFIG_INVALID")
        self._keys = MappingProxyType(dict(keys))

    def verify(
        self,
        signed: SignedGates,
        expected: GateContext,
        manifest: EvidenceManifest,
        artifacts: tuple[ArtifactInput, ...],
        *,
        now: datetime,
    ) -> AuthenticatedVerdict:
        """Check signature, freshness, exact context, bytes and mandatory policy."""
        try:
            signed = SignedGates.model_validate(signed.model_dump())
            expected = GateContext.model_validate(expected.model_dump())
            manifest = EvidenceManifest.model_validate(manifest.model_dump())
            if len(canonical(signed)) > 16384 or not 1 <= len(artifacts) <= 32:
                raise GateDenied("GATE_SIZE_DENIED")
            artifacts = tuple(
                ArtifactInput.model_validate(a.model_dump()) for a in artifacts
            )
            key = self._keys.get(signed.key_id)
            if key is None:
                raise GateDenied("GATE_KEY_UNKNOWN")
            key.verify(
                bytes.fromhex(signed.signature), canonical(signed, unsigned=True)
            )
        except (ValidationError, InvalidSignature, ValueError):
            raise GateDenied("GATE_SIGNATURE_OR_SCHEMA_INVALID") from None
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise GateDenied("GATE_CLOCK_INVALID")
        payload = signed.payload
        if not -5 <= (now - payload.observed_at).total_seconds() <= 300:
            raise GateDenied("GATE_FRESHNESS_DENIED")
        if payload.origin != "REMOTE_SERVICE":
            raise GateDenied("GATE_TEST_EVIDENCE_DENIED")
        if payload.context != expected:
            raise GateDenied("GATE_CONTEXT_DENIED")
        if (
            manifest.manifest_id != expected.manifest_ref
            or manifest.workspace_sha256 != expected.workspace_sha256
            or not verify_manifest(manifest, artifacts)
        ):
            raise GateDenied("GATE_MANIFEST_DENIED")
        available = {entry.artifact_id for entry in manifest.entries}
        if not {expected.patch_ref, expected.test_ref} <= available:
            raise GateDenied("GATE_PATCH_OR_TEST_ARTIFACT_MISSING")
        records = payload.gates
        for record in records:
            if (
                record.execution_ref not in available
                or record.source_commit != expected.source_commit
                or record.patch_ref != expected.patch_ref
                or record.workspace_sha256 != expected.workspace_sha256
            ):
                raise GateDenied("GATE_EVIDENCE_BINDING_DENIED")
        execution_gates = {
            "BASELINE_REPRODUCED",
            "PATCHED_REGRESSION_PASS",
            "CLEAN_REPLAY_PASS",
        }
        executions = [r for r in records if r.gate in execution_gates]
        if (
            any(
                r.execution_id is None or r.evidence_origin != "REMOTE_SERVICE"
                for r in executions
            )
            or len({r.execution_id for r in executions}) != len(executions)
            or len({r.execution_ref for r in executions}) != len(executions)
        ):
            raise GateDenied("GATE_INDEPENDENT_EXECUTIONS_DENIED")
        preview = evaluate(records)
        return AuthenticatedVerdict(
            status="VERIFIED"
            if preview.status == "GATES_SATISFIED"
            else preview.status,
            missing=preview.missing,
            failed=preview.failed,
            context=expected,
            observed_at=now,
            attestation_ref="sha256:" + hashlib.sha256(canonical(signed)).hexdigest(),
            manifest_ref=manifest.manifest_id,
        )
