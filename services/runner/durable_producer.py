"""Measured backend wired to fenced, restart-safe private producer records."""

from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.contracts.sandbox import RunnerError
from services.persistence.baseline_producer import BaselineProducerJournal
from services.persistence.producer import ProducerJournal
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.prepared import PreparedImageBinding
from services.runner.producer import MeasuredPreparedBackend, ProducedReceipt
from services.runner.remote import SandboxConfig
from services.runner.workspace import WorkspaceMeasurement


class DurableMeasuredBackend(MeasuredPreparedBackend):
    """Resume only journal-owned operations; unknown submissions stay sticky.

    Inject through dispatch_replay. Preparation is committed before POST; receipt
    and audit commit before return. No public attach-operation/signing interface,
    automatic polling, hidden resubmission, stage advance or live key discovery.
    """

    def __init__(
        self,
        config: SandboxConfig,
        binding: PreparedImageBinding,
        attestation: CapabilityAttestation,
        verifier: CapabilityVerifier,
        *,
        receipt_key: Ed25519PrivateKey,
        receipt_key_id: str,
        journal: ProducerJournal | BaselineProducerJournal,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Reject inconsistent trusted wiring before constructing HTTP resources.
        if (
            not isinstance(receipt_key, Ed25519PrivateKey)
            or journal.binding != binding
            or journal.key_id != receipt_key_id
            or journal.key.public_bytes_raw()
            != receipt_key.public_key().public_bytes_raw()
            or journal.origin
            != ("TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE")
        ):
            raise RunnerError("S_PRODUCER_CONFIGURATION_DENIED")
        super().__init__(
            config,
            binding,
            attestation,
            verifier,
            receipt_key=receipt_key,
            receipt_key_id=receipt_key_id,
            allow_live=allow_live,
            transport=transport,
        )
        self._journal = journal

    def _measure(self, image: UUID) -> WorkspaceMeasurement:
        if image != self._binding.image_uuid:
            return super()._measure(image)
        self._journal.guard_submission()
        source = super()._measure(image)
        self._journal.retain_preparation(source)
        return source

    def collect_receipt(self, operation_id: UUID) -> ProducedReceipt:
        if self._closed:
            raise RunnerError("S_CLOSED")
        plan, source, cached = self._journal.restore(operation_id)
        if self._operation is None:
            # Existing durable dispatch owns this ID. No setter accepts arbitrary
            # IDs without the journal's current lease/source/consent bindings.
            self._validate_submission(plan, self._binding.execution_id, source.tree_ref)
            self._operation = operation_id
            self._submitted = True
            self._measured_plan, self._source = plan, source
            self._exports_junit = True
        self._owned(operation_id)
        if cached is not None:
            self._receipt = cached
            return cached
        result = super().collect_receipt(operation_id)
        self._receipt = self._journal.retain_receipt(result)
        return self._receipt
