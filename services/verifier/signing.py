"""Externally provisioned signing boundary for immutable gate envelopes."""

import re
from collections.abc import Callable

from services.runner.receipts import canonical
from services.verifier.authenticated import GatePayload, SignedGates


class GateSigningError(Exception):
    """Stable signing failure without exposing external signer details."""


class ProductionGateSigner:
    """Sign production gate payloads through an injected external signer.

    This adapter owns no key material and never generates a key. The supplied
    callback should be backed by an operator-provisioned KMS/HSM signer. The
    matching public key must separately be configured in ``GateVerifier``.
    """

    def __init__(self, key_id: str, sign_bytes: Callable[[bytes], bytes]) -> None:
        if (
            not isinstance(key_id, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key_id) is None
            or not callable(sign_bytes)
        ):
            raise GateSigningError("GATE_SIGNER_UNAVAILABLE")
        self.key_id = key_id
        self._sign_bytes = sign_bytes

    def sign(self, payload: GatePayload) -> SignedGates:
        """Return a canonical Ed25519 envelope, failing closed on signer faults."""
        try:
            payload = GatePayload.model_validate(payload.model_dump())
            if payload.origin != "REMOTE_SERVICE":
                raise GateSigningError("GATE_TEST_EVIDENCE_DENIED")
            unsigned = SignedGates(
                payload=payload, key_id=self.key_id, signature="0" * 128
            )
            signature = self._sign_bytes(canonical(unsigned, unsigned=True))
            if not isinstance(signature, bytes) or len(signature) != 64:
                raise GateSigningError("GATE_SIGNER_RESPONSE_INVALID")
            return unsigned.model_copy(update={"signature": signature.hex()})
        except GateSigningError:
            raise
        except Exception:
            raise GateSigningError("GATE_SIGNING_FAILED") from None
