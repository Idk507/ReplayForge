"""Signer boundary tests use a disposable key and are not live evidence."""

from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.runner.receipts import canonical
from services.verifier.authenticated import GatePayload, GateVerifier
from services.verifier.signing import GateSigningError, ProductionGateSigner
from tests.unit.test_authenticated_gates import prepared


def test_external_signer_produces_canonical_verifiable_remote_envelope():
    _, signed, _, manifest, artifacts, _ = prepared()
    key = Ed25519PrivateKey.generate()
    payload = signed.payload
    signer = ProductionGateSigner("fixture-kms-key", key.sign)

    result = signer.sign(payload)

    assert result.key_id == "fixture-kms-key"
    assert canonical(result) == canonical(result.model_validate(result.model_dump()))
    verifier = GateVerifier({"fixture-kms-key": key.public_key()})
    assert (
        verifier.verify(
            result, payload.context, manifest, artifacts, now=datetime.now(UTC)
        ).status
        == "VERIFIED"
    )


def test_external_signer_rejects_test_transport_before_signing():
    _, signed, _, _, _, _ = prepared()
    signer = ProductionGateSigner("fixture-kms-key", Ed25519PrivateKey.generate().sign)
    payload = GatePayload.model_validate(
        signed.payload.model_dump() | {"origin": "TEST_TRANSPORT"}
    )

    with pytest.raises(GateSigningError, match="GATE_TEST_EVIDENCE_DENIED"):
        signer.sign(payload)


def test_external_signer_failure_is_redacted_and_fail_closed():
    _, signed, *_ = prepared()

    def unavailable(_: bytes) -> bytes:
        raise RuntimeError("private HSM diagnostics")

    signer = ProductionGateSigner("fixture-kms-key", unavailable)
    with pytest.raises(GateSigningError, match="GATE_SIGNING_FAILED") as error:
        signer.sign(signed.payload)
    assert "private HSM diagnostics" not in str(error.value)
