"""Signed capability gate only; never a workload execution authorization."""

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Self
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from packages.contracts.execution import TestCommandPlan
from packages.contracts.run import ArtifactID
from services.runner.receipts import canonical, plan_ref

Control = Literal[
    "remote_linux",
    "image_digest_verified",
    "non_root",
    "read_only_root",
    "no_privileged_mode",
    "no_host_mounts",
    "syscall_filter",
    "offline_execution",
    "metadata_denied",
    "control_plane_denied",
    "no_inherited_credentials",
    "cpu_enforced",
    "memory_enforced",
    "pids_enforced",
    "wall_timeout_enforced",
    "writable_bytes_enforced",
    "output_bytes_enforced",
    "file_count_enforced",
    "ephemeral_workspace",
    "owned_termination",
    "artifact_integrity",
    "retention_policy",
]
REQUIRED_CONTROLS: tuple[Control, ...] = (
    "remote_linux",
    "image_digest_verified",
    "non_root",
    "read_only_root",
    "no_privileged_mode",
    "no_host_mounts",
    "syscall_filter",
    "offline_execution",
    "metadata_denied",
    "control_plane_denied",
    "no_inherited_credentials",
    "cpu_enforced",
    "memory_enforced",
    "pids_enforced",
    "wall_timeout_enforced",
    "writable_bytes_enforced",
    "output_bytes_enforced",
    "file_count_enforced",
    "ephemeral_workspace",
    "owned_termination",
    "artifact_integrity",
    "retention_policy",
)
Backend = Literal["NEBIUS_CONTREE", "REMOTE_LINUX"]
Origin = Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]


class AdmissionDenied(Exception):
    """Stable safe denial without leaking profiles, signatures or provider data."""


class ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class ControlEvidence(ClosedModel):
    control: Control
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    evidence_ref: ArtifactID | None = None

    @model_validator(mode="after")
    def evidenced_pass(self) -> Self:
        if self.status == "PASS" and self.evidence_ref is None:
            raise ValueError("CAPABILITY_EVIDENCE_REQUIRED")
        return self


class RunnerLimits(ClosedModel):
    """Claimed enforced limits, distinct from requests and provider defaults."""

    cpu_millis: int = Field(strict=True, ge=1, le=64000)
    memory_bytes: int = Field(strict=True, ge=1, le=2**40)
    pids: int = Field(strict=True, ge=1, le=65536)
    wall_seconds: int = Field(strict=True, ge=1, le=3600)
    writable_bytes: int = Field(strict=True, ge=1, le=2**40)
    output_bytes: int = Field(strict=True, ge=1, le=16777216)
    file_count: int = Field(strict=True, ge=1, le=1000000)


class RunnerCapabilities(ClosedModel):
    """An independent assessor's claims about one runner and exact OCI image."""

    policy_version: Literal["runner-admission-v1"] = "runner-admission-v1"
    runner_id: UUID
    backend: Backend
    image_digest: ArtifactID
    configuration_ref: ArtifactID
    origin: Origin
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    controls: tuple[ControlEvidence, ...] = Field(max_length=22)
    limits: RunnerLimits | None = None

    @model_validator(mode="after")
    def unique_controls(self) -> Self:
        if len({c.control for c in self.controls}) != len(self.controls):
            raise ValueError("CAPABILITY_DUPLICATE_CONTROL")
        return self


class CapabilityAttestation(ClosedModel):
    purpose: Literal["replayforge.runner-capabilities.v1"] = (
        "replayforge.runner-capabilities.v1"
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    payload: RunnerCapabilities
    signature: str = Field(pattern=r"^[a-f0-9]{128}$", repr=False)


class CapabilityDecision(ClosedModel):
    """Satisfies only a capability gate, never the full dispatch decision."""

    status: Literal["DENIED", "CAPABILITY_REQUIREMENTS_MET"]
    observed_at: AwareDatetime
    trace_id: UUID
    policy_decision: Literal[
        "DENY_RUNNER_CAPABILITIES", "ALLOW_CAPABILITY_REQUIREMENTS_ONLY"
    ]
    plan_ref: ArtifactID
    attestation_ref: ArtifactID
    missing_controls: tuple[Control, ...]
    reasons: tuple[
        Literal[
            "TEST_EVIDENCE",
            "MISSING_CONTROLS",
            "MISSING_LIMITS",
            "LIMITS_EXCEED_POLICY",
        ],
        ...,
    ]
    retryable: Literal[False] = False
    error_class: None = None
    artifacts: tuple[ArtifactID, ...]
    can_authorize_execution: Literal[False] = False


class CapabilityVerifier:
    """Operator-provisioned assessor keys scoped to runner/backend/origin.

    Keys must belong to an independent control assessor, outside untrusted
    workloads. No discovery, signing, provider requests or production keys exist
    here. A signature authenticates the assessor, not the underlying test facts.
    """

    def __init__(
        self,
        keys: Mapping[str, Ed25519PublicKey],
        *,
        runner_id: UUID,
        backend: Backend,
        origin: Origin,
        configuration_ref: str,
    ) -> None:
        if (
            not isinstance(keys, Mapping)
            or not isinstance(runner_id, UUID)
            or backend not in {"NEBIUS_CONTREE", "REMOTE_LINUX"}
            or origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}
            or not isinstance(configuration_ref, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", configuration_ref)
            or not 1 <= len(keys) <= 16
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)
                or not isinstance(key, Ed25519PublicKey)
                for name, key in keys.items()
            )
        ):
            raise AdmissionDenied("CAPABILITY_CONFIG_INVALID")
        self._keys = MappingProxyType(dict(keys))
        self._runner_id = runner_id
        self._backend = backend
        self._origin = origin
        self._configuration_ref = configuration_ref

    def assess(
        self,
        signed: CapabilityAttestation,
        plan: TestCommandPlan,
        *,
        now: datetime,
    ) -> CapabilityDecision:
        """Verify signature, scope, lifetime and all mandatory controls/limits."""
        try:
            signed = CapabilityAttestation.model_validate(signed.model_dump())
            plan = TestCommandPlan.model_validate(plan.model_dump())
            body = canonical(signed)
            if len(body) > 16384:
                raise AdmissionDenied("CAPABILITY_SIZE_DENIED")
            key = self._keys.get(signed.key_id)
            if key is None:
                raise AdmissionDenied("CAPABILITY_KEY_UNKNOWN")
            key.verify(
                bytes.fromhex(signed.signature), canonical(signed, unsigned=True)
            )
        except (InvalidSignature, ValidationError, ValueError):
            raise AdmissionDenied("CAPABILITY_SIGNATURE_INVALID") from None
        profile = signed.payload
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise AdmissionDenied("CAPABILITY_CLOCK_INVALID")
        lifetime = (profile.expires_at - profile.issued_at).total_seconds()
        if (
            not 0 < lifetime <= 86400
            or now >= profile.expires_at
            or (profile.issued_at - now).total_seconds() > 5
        ):
            raise AdmissionDenied("CAPABILITY_FRESHNESS_DENIED")
        if (
            profile.runner_id != self._runner_id
            or profile.backend != self._backend
            or profile.origin != self._origin
            or profile.image_digest != plan.request.image_digest
            or profile.configuration_ref != self._configuration_ref
        ):
            raise AdmissionDenied("CAPABILITY_BINDING_DENIED")
        passed = {c.control for c in profile.controls if c.status == "PASS"}
        missing = tuple(c for c in REQUIRED_CONTROLS if c not in passed)
        reasons: list[
            Literal[
                "TEST_EVIDENCE",
                "MISSING_CONTROLS",
                "MISSING_LIMITS",
                "LIMITS_EXCEED_POLICY",
            ]
        ] = []
        if profile.origin != "REMOTE_SERVICE":
            reasons.append("TEST_EVIDENCE")
        if missing:
            reasons.append("MISSING_CONTROLS")
        limits = profile.limits
        if limits is None:
            reasons.append("MISSING_LIMITS")
        elif (
            limits.cpu_millis > 2000
            or limits.memory_bytes > 512 * 1024 * 1024
            or limits.pids > 64
            or limits.wall_seconds > min(60, plan.timeout_seconds)
            or limits.writable_bytes > 64 * 1024 * 1024
            or limits.output_bytes > 131072
            or limits.file_count > 1024
        ):
            reasons.append("LIMITS_EXCEED_POLICY")
        reference = "sha256:" + hashlib.sha256(body).hexdigest()
        return CapabilityDecision(
            status="DENIED" if reasons else "CAPABILITY_REQUIREMENTS_MET",
            observed_at=now,
            trace_id=plan.trace_id,
            policy_decision="DENY_RUNNER_CAPABILITIES"
            if reasons
            else "ALLOW_CAPABILITY_REQUIREMENTS_ONLY",
            plan_ref=plan_ref(plan),
            attestation_ref=reference,
            missing_controls=missing,
            reasons=tuple(reasons),
            artifacts=(reference,),
        )


def recheck_retained_capability(
    payload: bytes,
    reference: ArtifactID,
    plan: TestCommandPlan,
    verifier: CapabilityVerifier,
    *,
    now: datetime,
) -> tuple[CapabilityAttestation, CapabilityDecision]:
    """Revalidate exact retained assessor bytes before gate derivation.

    A digest alone is not an attestation. This requires canonical signed bytes,
    a matching immutable reference, the configured assessor key, the exact
    phase plan and a fresh trusted clock.
    """
    if (
        not isinstance(payload, bytes)
        or not 1 <= len(payload) <= 16384
        or not isinstance(reference, str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", reference)
        or hashlib.sha256(payload).hexdigest() != reference[7:]
    ):
        raise AdmissionDenied("CAPABILITY_RETAINED_BYTES_INVALID")
    try:
        attestation = CapabilityAttestation.model_validate_json(payload)
        if canonical(attestation) != payload:
            raise AdmissionDenied("CAPABILITY_RETAINED_BYTES_INVALID")
    except (ValidationError, ValueError):
        raise AdmissionDenied("CAPABILITY_RETAINED_BYTES_INVALID") from None
    decision = verifier.assess(attestation, plan, now=now)
    if decision.status != "CAPABILITY_REQUIREMENTS_MET":
        raise AdmissionDenied("CAPABILITY_RECHECK_DENIED")
    return attestation, decision
