"""Build existing verification trust objects from operator-owned deployment data.

The JSON file contains public trust anchors and runtime bindings only. Signing
is delegated to an operator-provisioned secret-manager/KMS adapter module.
Nothing in this module generates, reads, persists, or logs private key bytes.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.run import ArtifactID
from services.orchestrator.production_verification import PHASES, PhaseTrust
from services.orchestrator.verification_worker import VerificationTrust
from services.runner.admission import CapabilityVerifier
from services.runner.receipts import ReceiptVerifier
from services.verifier.authenticated import GateVerifier
from services.verifier.signing import ProductionGateSigner

_KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TEST_ID = re.compile(
    r"(^|[-_.])(test|tests|synthetic|fixture|development)([-_.]|$)", re.I
)
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,190}$")
_MAX_CONFIG_BYTES = 65536
_MAX_KEY_LIFETIME = timedelta(days=366)
_MIN_REMAINING_LIFETIME = timedelta(days=7)


class DeploymentTrustError(Exception):
    """Safe trust-configuration error code without input values."""


class SigningBackend(Protocol):
    """Operator module backed by a secret manager or KMS/HSM."""

    key_id: str
    public_key: Ed25519PublicKey

    def sign_bytes(self, message: bytes) -> bytes: ...


class TrustKeyConfig(BaseModel):
    """Public verification identity with an explicit active validity interval."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    algorithm: Literal["Ed25519"]
    public_key_pem: str = Field(min_length=80, max_length=4096, repr=False)
    not_before: AwareDatetime
    not_after: AwareDatetime


class PhaseTrustConfig(BaseModel):
    """Trust anchors and immutable runner identity for one execution phase."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    image_uuid: UUID
    image_digest: ArtifactID
    configuration_ref: ArtifactID
    runner_id: UUID
    backend: Literal["NEBIUS_CONTREE", "REMOTE_LINUX"]
    receipt_keys: tuple[TrustKeyConfig, ...] = Field(min_length=1, max_length=16)
    assessor_keys: tuple[TrustKeyConfig, ...] = Field(min_length=1, max_length=16)


class DeploymentTrustConfig(BaseModel):
    """Strict public trust document; no private key or provider key discovery."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["replayforge-verification-trust-v1"]
    gate_signer: TrustKeyConfig
    gate_keys: tuple[TrustKeyConfig, ...] = Field(min_length=1, max_length=16)
    phases: dict[
        Literal["BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"],
        PhaseTrustConfig,
    ]


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _public_key_material(config: TrustKeyConfig) -> bytes:
    """Return canonical raw Ed25519 bytes for cross-role alias detection."""
    key = _ed25519_key(config)
    return key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _parse_config(raw: bytes, *, now: datetime) -> DeploymentTrustConfig:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise DeploymentTrustError("VERIFICATION_TRUST_CLOCK_INVALID")
    if not 1 <= len(raw) <= _MAX_CONFIG_BYTES:
        raise DeploymentTrustError("VERIFICATION_TRUST_CONFIG_INVALID")
    try:
        decoded = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
        config = DeploymentTrustConfig.model_validate(decoded)
        if set(config.phases) != set(PHASES):
            raise ValueError("phase set")
        all_keys = [config.gate_signer, *config.gate_keys]
        for phase in PHASES:
            item = config.phases[phase]
            all_keys.extend(item.receipt_keys)
            all_keys.extend(item.assessor_keys)
        for key in all_keys:
            lifetime = key.not_after - key.not_before
            if (
                _KEY_ID.fullmatch(key.key_id) is None
                or _TEST_ID.search(key.key_id) is not None
                or lifetime <= timedelta(0)
                or lifetime > _MAX_KEY_LIFETIME
                or key.not_before > now
                or key.not_after <= now + _MIN_REMAINING_LIFETIME
            ):
                raise ValueError("key lifetime or identity")
        for phase in PHASES:
            item = config.phases[phase]
            if len({key.key_id for key in item.receipt_keys}) != len(
                item.receipt_keys
            ) or len({key.key_id for key in item.assessor_keys}) != len(
                item.assessor_keys
            ):
                raise ValueError("duplicate phase key ID")
        if len({key.key_id for key in config.gate_keys}) != len(config.gate_keys):
            raise ValueError("duplicate gate key ID")
        gate_ids = {key.key_id for key in config.gate_keys}
        if config.gate_signer.key_id not in gate_ids:
            raise ValueError("gate signer key ID")
        collector_ids = {
            key.key_id for phase in PHASES for key in config.phases[phase].receipt_keys
        }
        assessor_ids = {
            key.key_id for phase in PHASES for key in config.phases[phase].assessor_keys
        }
        if gate_ids & (collector_ids | assessor_ids) or collector_ids & assessor_ids:
            raise ValueError("trust roles must use separate key IDs")
        gate_material = {_public_key_material(key) for key in config.gate_keys}
        collector_material = {
            _public_key_material(key)
            for phase in PHASES
            for key in config.phases[phase].receipt_keys
        }
        assessor_material = {
            _public_key_material(key)
            for phase in PHASES
            for key in config.phases[phase].assessor_keys
        }
        if (
            gate_material & (collector_material | assessor_material)
            or collector_material & assessor_material
        ):
            raise ValueError("trust roles must use separate public keys")
        return config
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        raise DeploymentTrustError("VERIFICATION_TRUST_CONFIG_INVALID") from None


def _ed25519_key(config: TrustKeyConfig) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(config.public_key_pem.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        raise DeploymentTrustError("VERIFICATION_TRUST_PUBLIC_KEY_INVALID") from None
    if not isinstance(key, Ed25519PublicKey):
        raise DeploymentTrustError("VERIFICATION_TRUST_ALGORITHM_DENIED")
    return key


def _signing_backend(module_path: str, expected_key: TrustKeyConfig) -> SigningBackend:
    if _MODULE.fullmatch(module_path) is None:
        raise DeploymentTrustError("VERIFICATION_SIGNER_PROVIDER_INVALID")
    try:
        module = importlib.import_module(module_path)
        backend = module.provide_signing_backend()
    except Exception:
        raise DeploymentTrustError("VERIFICATION_SIGNER_UNAVAILABLE") from None
    if (
        not callable(getattr(backend, "sign_bytes", None))
        or not isinstance(getattr(backend, "key_id", None), str)
        or backend.key_id != expected_key.key_id
        or not isinstance(getattr(backend, "public_key", None), Ed25519PublicKey)
        or backend.public_key != _ed25519_key(expected_key)
    ):
        raise DeploymentTrustError("VERIFICATION_SIGNER_IDENTITY_MISMATCH")
    return cast(SigningBackend, backend)


def build_verification_trust(
    raw_config: bytes,
    backend: SigningBackend,
    *,
    now: datetime | None = None,
) -> VerificationTrust:
    """Compose existing signer/verifier APIs from validated provisioned keys."""
    config = _parse_config(raw_config, now=now or datetime.now(UTC))
    gate_keys = {item.key_id: _ed25519_key(item) for item in config.gate_keys}
    if (
        not callable(getattr(backend, "sign_bytes", None))
        or getattr(backend, "key_id", None) != config.gate_signer.key_id
        or getattr(backend, "public_key", None) != _ed25519_key(config.gate_signer)
        or gate_keys.get(config.gate_signer.key_id) != _ed25519_key(config.gate_signer)
    ):
        raise DeploymentTrustError("VERIFICATION_SIGNER_IDENTITY_MISMATCH")
    try:
        signer = ProductionGateSigner(config.gate_signer.key_id, backend.sign_bytes)
        gate_verifier = GateVerifier(gate_keys)
        phases: dict[str, PhaseTrust] = {}
        for phase in PHASES:
            item = config.phases[phase]
            receipt_keys = {key.key_id: _ed25519_key(key) for key in item.receipt_keys}
            assessor_keys = {
                key.key_id: _ed25519_key(key) for key in item.assessor_keys
            }
            phases[phase] = PhaseTrust(
                image_uuid=item.image_uuid,
                image_digest=item.image_digest,
                configuration_ref=item.configuration_ref,
                runner_id=item.runner_id,
                receipt_verifier=ReceiptVerifier(receipt_keys, origin="REMOTE_SERVICE"),
                capability_verifier=CapabilityVerifier(
                    assessor_keys,
                    runner_id=item.runner_id,
                    backend=item.backend,
                    origin="REMOTE_SERVICE",
                    configuration_ref=item.configuration_ref,
                ),
            )
        return VerificationTrust(
            phase_trust=phases,
            signer=signer,
            gate_verifier=gate_verifier,
        )
    except (ValueError, TypeError):
        raise DeploymentTrustError("VERIFICATION_TRUST_CONFIG_INVALID") from None


def provide_verification_trust(
    values: Mapping[str, str] | None = None,
) -> VerificationTrust:
    """Load public anchors and an external signer without accepting run data."""
    env = os.environ if values is None else values
    config_path = env.get("REPLAYFORGE_VERIFICATION_TRUST_CONFIG", "")
    signer_module = env.get("REPLAYFORGE_VERIFICATION_SIGNER_PROVIDER", "")
    path = Path(config_path)
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError
        raw = path.read_bytes()
        config = _parse_config(raw, now=datetime.now(UTC))
        backend = _signing_backend(signer_module, config.gate_signer)
        return build_verification_trust(raw, backend)
    except DeploymentTrustError:
        raise
    except (OSError, ValueError, TypeError):
        raise DeploymentTrustError("VERIFICATION_TRUST_CONFIG_UNAVAILABLE") from None
