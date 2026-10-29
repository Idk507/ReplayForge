"""Azure ES256 tests use only local synthetic P-256 identities and fake SDKs."""

from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from services.runner.receipts import canonical
from services.verifier.authenticated import GateDenied, GatePayload
from services.verifier.azure_es256 import (
    AzureES256Error,
    AzureES256GateSignerV5,
    AzureES256GateVerifierV5,
    AzureES256KeyVaultBackend,
    AzureES256TrustKey,
)
from tests.unit.test_authenticated_gates import prepared


class FakeAzureCryptographyClient:
    def __init__(self, key_version: str, private_key: ec.EllipticCurvePrivateKey):
        self.key_version = key_version
        self.private_key = private_key
        self.algorithm = "ES256"

    def sign(self, algorithm: str, digest: bytes):
        if algorithm != self.algorithm:
            raise ValueError("algorithm mismatch")
        der = self.private_key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        r, s = utils.decode_dss_signature(der)
        return type(
            "SignResult",
            (),
            {
                "key_id": self.key_version,
                "algorithm": self.algorithm,
                "signature": r.to_bytes(32, "big") + s.to_bytes(32, "big"),
            },
        )()


@pytest.fixture
def azure_profile():
    _, signed, _, manifest, artifacts, _ = prepared()
    key_version = "https://demo.vault.azure.net/keys/replayforge-gate/00000000000000000000000000000001"
    private_key = ec.generate_private_key(ec.SECP256R1())
    backend = AzureES256KeyVaultBackend(
        key_id="azure-gate-v5",
        key_version=key_version,
        public_key=private_key.public_key(),
        client=FakeAzureCryptographyClient(key_version, private_key),
    )
    signer = AzureES256GateSignerV5(backend)
    trust = AzureES256GateVerifierV5(
        {
            "azure-gate-v5": AzureES256TrustKey(
                key_version=key_version,
                public_key=private_key.public_key(),
            )
        }
    )
    return signed.payload, manifest, artifacts, signer, trust, key_version


def test_azure_es256_v5_signs_canonical_payload_and_verifies_all_gates(azure_profile):
    payload, manifest, artifacts, signer, verifier, key_version = azure_profile
    signed = signer.sign(payload)
    result = verifier.verify(
        signed,
        payload.context,
        manifest,
        artifacts,
        now=datetime.now(UTC),
    )
    assert signed.purpose == "replayforge.verification-gates.v5"
    assert signed.algorithm == "ES256"
    assert signed.key_version == key_version
    assert len(bytes.fromhex(signed.signature)) == 64
    assert result.status == "VERIFIED"
    assert (
        verifier.verify(
            signed.model_validate_json(canonical(signed)),
            payload.context,
            manifest,
            artifacts,
            now=datetime.now(UTC),
        ).attestation_ref
        == result.attestation_ref
    )


def test_azure_es256_v5_rejects_tampering_and_key_version_substitution(azure_profile):
    payload, manifest, artifacts, signer, verifier, key_version = azure_profile
    signed = signer.sign(payload)
    changed = signed.model_copy(update={"signature": "0" * 128})
    with pytest.raises(GateDenied):
        verifier.verify(
            changed,
            payload.context,
            manifest,
            artifacts,
            now=datetime.now(UTC),
        )

    other_version = signed.model_copy(update={"key_version": key_version[:-1] + "2"})
    with pytest.raises(GateDenied):
        verifier.verify(
            other_version,
            payload.context,
            manifest,
            artifacts,
            now=datetime.now(UTC),
        )


def test_azure_es256_v5_rejects_test_transport_and_algorithm_substitution(
    azure_profile,
):
    payload, _, _, signer, _, _ = azure_profile
    test_payload = GatePayload.model_validate(
        payload.model_dump() | {"origin": "TEST_TRANSPORT"}
    )
    with pytest.raises(AzureES256Error, match="TEST_EVIDENCE"):
        signer.sign(test_payload)

    with pytest.raises(AzureES256Error, match="RESPONSE_INVALID"):
        AzureES256GateSignerV5(
            AzureES256KeyVaultBackend(
                key_id="azure-gate-v5",
                key_version="https://demo.vault.azure.net/keys/replayforge-gate/00000000000000000000000000000002",
                public_key=ec.generate_private_key(ec.SECP256R1()).public_key(),
                client=type(
                    "BadClient",
                    (),
                    {
                        "sign": lambda self, algorithm, digest: type(
                            "SignResult",
                            (),
                            {
                                "key_id": "https://demo.vault.azure.net/keys/replayforge-gate/00000000000000000000000000000002",
                                "algorithm": "ES384",
                                "signature": b"x" * 64,
                            },
                        )(),
                    },
                )(),
            )
        ).sign(payload)


def test_azure_es256_trust_rejects_non_p256_key_and_unpinned_version():
    with pytest.raises(ValueError, match="P256"):
        AzureES256TrustKey(
            key_version="https://demo.vault.azure.net/keys/k/00000000000000000000000000000001",
            public_key=ec.generate_private_key(ec.SECP384R1()).public_key(),
        )


def test_azure_es256_environment_loader_fails_closed_without_exact_key_version():
    from services.verifier.azure_es256 import AzureES256KeyVaultBackend

    with pytest.raises(AzureES256Error, match="CONFIG_INVALID"):
        AzureES256KeyVaultBackend.from_environment(
            {"REPLAYFORGE_AZURE_GATE_KEY_ID": "gate-prod"}
        )

    with pytest.raises(AzureES256Error, match="CONFIG_INVALID"):
        AzureES256KeyVaultBackend.from_environment(
            {
                "REPLAYFORGE_AZURE_GATE_KEY_ID": "gate-prod",
                "REPLAYFORGE_AZURE_GATE_KEY_VERSION": "https://demo.vault.azure.net/keys/gate",
            }
        )


def test_azure_es256_environment_loader_rejects_cli_fallback_identity():
    from services.verifier.azure_es256 import AzureES256KeyVaultBackend

    base = {
        "REPLAYFORGE_AZURE_GATE_KEY_ID": "gate-prod",
        "REPLAYFORGE_AZURE_GATE_KEY_VERSION": (
            "https://demo.vault.azure.net/keys/gate/00000000000000000000000000000001"
        ),
    }
    with pytest.raises(AzureES256Error, match="CONFIG_INVALID"):
        AzureES256KeyVaultBackend.from_environment(
            base | {"REPLAYFORGE_AZURE_CREDENTIAL_MODE": "azure_cli"}
        )

    with pytest.raises(AzureES256Error, match="IDENTITY_CONFIG_INVALID"):
        AzureES256KeyVaultBackend.from_environment(
            base | {"REPLAYFORGE_AZURE_CREDENTIAL_MODE": "workload_identity"}
        )
