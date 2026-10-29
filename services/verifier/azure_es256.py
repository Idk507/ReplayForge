"""Separate Azure Key Vault ES256 gate profile (envelope v5).

This module never generates or exports private key material. It is intentionally
not interchangeable with the existing Ed25519 v4 signer or verifier.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.runner.receipts import canonical
from services.verifier.authenticated import (
    AuthenticatedVerdict,
    GateContext,
    GateDenied,
    GatePayload,
    GateVerifier,
)
from services.verifier.manifest import ArtifactInput, EvidenceManifest

_KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_KEY_VERSION = re.compile(
    r"^https://[A-Za-z0-9.-]+\.(?:vault\.azure\.net|managedhsm\.azure\.net)"
    r"/keys/[A-Za-z0-9-]{1,127}/[a-fA-F0-9]{32}$"
)
_HEX_SIGNATURE = re.compile(r"^[a-f0-9]{128}$")


class AzureES256Error(Exception):
    """Stable Azure signer failure without SDK details or sensitive values."""


class AzureSignClient(Protocol):
    """Small seam around the Azure Key Vault cryptography client."""

    def sign(self, algorithm: str, digest: bytes) -> object: ...


class _AzureSDKSignClient:
    """Adapt the Azure SDK enum/result to a minimal testable interface."""

    def __init__(self, client: object) -> None:
        self._client = client

    def sign(self, algorithm: str, digest: bytes) -> object:
        if algorithm != "ES256":
            raise AzureES256Error("AZURE_ES256_ALGORITHM_DENIED")
        from azure.keyvault.keys.crypto import SignatureAlgorithm

        return cast(Any, self._client).sign(SignatureAlgorithm.es256, digest)


class AzureES256GateEnvelopeV5(BaseModel):
    """Distinct signed gate envelope, pinned to an immutable Azure key version."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    purpose: Literal["replayforge.verification-gates.v5"] = (
        "replayforge.verification-gates.v5"
    )
    algorithm: Literal["ES256"] = "ES256"
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    key_version: str = Field(pattern=_KEY_VERSION.pattern)
    payload: GatePayload
    signature: str = Field(pattern=_HEX_SIGNATURE.pattern, repr=False)

    @classmethod
    def validate_profile(cls, value: object) -> AzureES256GateEnvelopeV5:
        envelope = cls.model_validate(value)
        if (
            envelope.purpose != "replayforge.verification-gates.v5"
            or envelope.algorithm != "ES256"
            or _KEY_VERSION.fullmatch(envelope.key_version) is None
        ):
            raise AzureES256Error("AZURE_ES256_ENVELOPE_INVALID")
        return envelope


class AzureES256KeyVaultBackend:
    """Key-version-pinned `sign_bytes` equivalent that only emits ES256.

    The method name is deliberately not `sign_bytes`: passing this backend to
    `ProductionGateSigner` must fail type/interface validation, preventing an
    ES256 result from being mislabeled as an Ed25519 v4 signature.
    """

    def __init__(
        self,
        *,
        key_id: str,
        key_version: str,
        public_key: ec.EllipticCurvePublicKey,
        client: AzureSignClient,
    ) -> None:
        if (
            not isinstance(key_id, str)
            or _KEY_ID.fullmatch(key_id) is None
            or not isinstance(key_version, str)
            or _KEY_VERSION.fullmatch(key_version) is None
            or not isinstance(public_key, ec.EllipticCurvePublicKey)
            or not isinstance(public_key.curve, ec.SECP256R1)
            or not callable(getattr(client, "sign", None))
        ):
            raise AzureES256Error("AZURE_ES256_TRUST_INVALID")
        self.key_id = key_id
        self.key_version = key_version
        self.public_key = public_key
        self._client = client

    def sign_envelope(self, unsigned: AzureES256GateEnvelopeV5) -> bytes:
        """Sign SHA-256 of the canonical, unsigned v5 envelope via Key Vault."""
        try:
            unsigned = AzureES256GateEnvelopeV5.validate_profile(unsigned)
            if unsigned.signature != "0" * 128:
                raise AzureES256Error("AZURE_ES256_UNSIGNED_ENVELOPE_REQUIRED")
            digest = hashlib.sha256(canonical(unsigned, unsigned=True)).digest()
            result = self._client.sign("ES256", digest)
            result_key_id = getattr(result, "key_id", None)
            result_algorithm = getattr(result, "algorithm", None)
            signature = getattr(result, "signature", None)
            if (
                result_key_id != self.key_version
                or str(result_algorithm).upper() != "ES256"
                or not isinstance(signature, bytes)
                or len(signature) != 64
            ):
                raise AzureES256Error("AZURE_ES256_RESPONSE_INVALID")
            return signature
        except AzureES256Error:
            raise
        except Exception:
            raise AzureES256Error("AZURE_ES256_SIGN_FAILED") from None

    @classmethod
    def from_environment(
        cls, values: Mapping[str, str] | None = None
    ) -> AzureES256KeyVaultBackend:
        """Load a pinned versioned key using Azure Identity; performs only GETs.

        Required identity: `REPLAYFORGE_AZURE_GATE_KEY_ID` and a fully versioned
        `REPLAYFORGE_AZURE_GATE_KEY_VERSION`. The host identity needs only key
        read/public-key and sign permissions. This factory creates no resources.
        """
        import os

        env = os.environ if values is None else values
        alias = env.get("REPLAYFORGE_AZURE_GATE_KEY_ID", "")
        key_version = env.get("REPLAYFORGE_AZURE_GATE_KEY_VERSION", "")
        credential_mode = env.get("REPLAYFORGE_AZURE_CREDENTIAL_MODE", "")
        if (
            _KEY_ID.fullmatch(alias) is None
            or _KEY_VERSION.fullmatch(key_version) is None
            or credential_mode not in {"managed_identity", "workload_identity"}
        ):
            raise AzureES256Error("AZURE_ES256_CONFIG_INVALID")
        parsed = urlsplit(key_version)
        name, version = parsed.path.strip("/").split("/")[-2:]
        try:
            from azure.keyvault.keys import KeyClient
            from azure.keyvault.keys.crypto import CryptographyClient

            credential: Any
            if credential_mode == "managed_identity":
                from azure.identity import ManagedIdentityCredential

                client_id = env.get("AZURE_CLIENT_ID") or None
                credential = ManagedIdentityCredential(client_id=client_id)
            else:
                from azure.identity import WorkloadIdentityCredential

                tenant_id = env.get("AZURE_TENANT_ID", "")
                client_id = env.get("AZURE_CLIENT_ID", "")
                token_file_path = env.get("AZURE_FEDERATED_TOKEN_FILE", "")
                if not all((tenant_id, client_id, token_file_path)):
                    raise AzureES256Error("AZURE_ES256_IDENTITY_CONFIG_INVALID")
                credential = WorkloadIdentityCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    token_file_path=token_file_path,
                )
            key_client = KeyClient(
                vault_url=f"{parsed.scheme}://{parsed.netloc}", credential=credential
            )
            key = key_client.get_key(name, version=version)
            key_id = getattr(key, "id", "")
            jwk = getattr(key, "key", None)
            key_type = _enum_value(getattr(jwk, "kty", None)).upper()
            curve = _enum_value(getattr(jwk, "crv", None)).upper()
            enabled = getattr(getattr(key, "properties", None), "enabled", None)
            key_ops = {
                _enum_value(item).lower()
                for item in (getattr(jwk, "key_ops", None) or ())
            }
            if (
                key_id != key_version
                or key_type not in {"EC-HSM", "EC_HSM"}
                or curve not in {"P-256", "P256"}
                or enabled is not True
                or "sign" not in key_ops
            ):
                raise AzureES256Error("AZURE_ES256_KEY_BINDING_DENIED")
            x = _decode_jwk_coordinate(getattr(jwk, "x", None))
            y = _decode_jwk_coordinate(getattr(jwk, "y", None))
            public_key = ec.EllipticCurvePublicNumbers(
                int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
            ).public_key()
            crypto = CryptographyClient(key_version, credential=credential)
            return cls(
                key_id=alias,
                key_version=key_version,
                public_key=public_key,
                client=_AzureSDKSignClient(crypto),
            )
        except AzureES256Error:
            raise
        except Exception:
            raise AzureES256Error("AZURE_ES256_TRUST_UNAVAILABLE") from None


class AzureES256GateSignerV5:
    """Create only the separate v5 envelope; never returns an Ed25519 v4 type."""

    def __init__(self, backend: AzureES256KeyVaultBackend) -> None:
        if not isinstance(backend, AzureES256KeyVaultBackend):
            raise AzureES256Error("AZURE_ES256_SIGNER_UNAVAILABLE")
        self.backend = backend

    @property
    def key_id(self) -> str:
        """Operator-provisioned alias used by worker identity checks."""
        return self.backend.key_id

    def sign(self, payload: GatePayload) -> AzureES256GateEnvelopeV5:
        """Sign remote-service evidence under the fixed v5 ES256 profile."""
        try:
            payload = GatePayload.model_validate(payload.model_dump())
            if payload.origin != "REMOTE_SERVICE":
                raise AzureES256Error("AZURE_ES256_TEST_EVIDENCE_DENIED")
            unsigned = AzureES256GateEnvelopeV5(
                key_id=self.backend.key_id,
                key_version=self.backend.key_version,
                payload=payload,
                signature="0" * 128,
            )
            signature = self.backend.sign_envelope(unsigned)
            return unsigned.model_copy(update={"signature": signature.hex()})
        except AzureES256Error:
            raise
        except Exception:
            raise AzureES256Error("AZURE_ES256_SIGN_FAILED") from None


class AzureES256TrustKey(BaseModel):
    """Independent ES256 verifier trust, pinned to one immutable key version."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    key_version: str = Field(pattern=_KEY_VERSION.pattern)
    public_key: ec.EllipticCurvePublicKey

    def model_post_init(self, __context: object) -> None:
        if _KEY_VERSION.fullmatch(self.key_version) is None or not isinstance(
            self.public_key.curve, ec.SECP256R1
        ):
            raise ValueError("AZURE_ES256_TRUST_INVALID_P256")


class AzureES256GateVerifierV5:
    """Independent v5 verifier; the Ed25519 v4 verifier remains unchanged."""

    def __init__(self, keys: Mapping[str, AzureES256TrustKey]) -> None:
        if (
            not isinstance(keys, Mapping)
            or not 1 <= len(keys) <= 16
            or any(
                not isinstance(key_id, str)
                or _KEY_ID.fullmatch(key_id) is None
                or not isinstance(value, AzureES256TrustKey)
                for key_id, value in keys.items()
            )
        ):
            raise GateDenied("AZURE_ES256_TRUST_INVALID")
        self._keys = dict(keys)

    def verify(
        self,
        signed: AzureES256GateEnvelopeV5,
        expected: GateContext,
        manifest: EvidenceManifest,
        artifacts: tuple[ArtifactInput, ...],
        *,
        now: datetime,
    ) -> AuthenticatedVerdict:
        """Verify version, pinned key, ES256 signature, then shared seven gates."""
        try:
            signed = AzureES256GateEnvelopeV5.validate_profile(signed.model_dump())
            expected = GateContext.model_validate(expected.model_dump())
            manifest = EvidenceManifest.model_validate(manifest.model_dump())
            if len(canonical(signed)) > 16384 or not 1 <= len(artifacts) <= 32:
                raise GateDenied("GATE_SIZE_DENIED")
            normalized_artifacts = tuple(
                ArtifactInput.model_validate(item.model_dump()) for item in artifacts
            )
            trust = self._keys.get(signed.key_id)
            if trust is None or trust.key_version != signed.key_version:
                raise GateDenied("AZURE_ES256_KEY_VERSION_DENIED")
            raw = bytes.fromhex(signed.signature)
            der = utils.encode_dss_signature(
                int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
            )
            digest = hashlib.sha256(canonical(signed, unsigned=True)).digest()
            trust.public_key.verify(
                der,
                digest,
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
        except GateDenied:
            raise
        except (ValidationError, InvalidSignature, ValueError, TypeError):
            raise GateDenied("GATE_SIGNATURE_OR_SCHEMA_INVALID") from None
        return GateVerifier._verify_authenticated_payload(
            signed.payload,
            expected,
            manifest,
            normalized_artifacts,
            now=now,
            signed_envelope=canonical(signed),
        )


def _enum_value(value: object) -> str:
    candidate = getattr(value, "value", value)
    return candidate if isinstance(candidate, str) else ""


def _decode_jwk_coordinate(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise AzureES256Error("AZURE_ES256_KEY_PUBLIC_MATERIAL_INVALID")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise AzureES256Error("AZURE_ES256_KEY_PUBLIC_MATERIAL_INVALID") from None
    if len(decoded) != 32:
        raise AzureES256Error("AZURE_ES256_KEY_PUBLIC_MATERIAL_INVALID")
    return decoded
