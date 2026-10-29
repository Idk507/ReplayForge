"""Explicit trusted inputs for durable confined baseline execution."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from packages.contracts.run import RunState, Stage
from packages.contracts.sandbox import RunnerError
from services.persistence.baseline import BaselineAction
from services.persistence.baseline_producer import BaselineProducerJournal
from services.persistence.store import Lease, Store
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.confined import DurableConfinedBackend
from services.runner.confinement import confinement_configuration_ref
from services.runner.prepared import PreparedImageBinding
from services.runner.receipts import ReceiptVerifier, plan_ref
from services.runner.remote import SandboxConfig


class ConfinedBaselineProvider:
    """No preparation, key discovery or network I/O during construction.

    Inputs belong to trusted deployment code, never a public request or model.
    This factory does not qualify its image or assess a capability claim.
    """

    def __init__(
        self,
        config: SandboxConfig,
        action: BaselineAction,
        binding: PreparedImageBinding,
        attestation: CapabilityAttestation,
        verifier: CapabilityVerifier,
        *,
        receipt_key: Ed25519PrivateKey,
        receipt_key_id: str,
        allow_privileged_setup: bool = False,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if transport is None and allow_live is not True:
            raise RunnerError("S_LIVE_NOT_AUTHORIZED")
        if allow_privileged_setup is not True:
            raise RunnerError("CONFINEMENT_SETUP_NOT_AUTHORIZED")
        if (
            not isinstance(receipt_key, Ed25519PrivateKey)
            or not isinstance(receipt_key_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", receipt_key_id)
        ):
            raise RunnerError("S_RECEIPT_KEY_INVALID")
        self._action = BaselineAction.model_validate(action.model_dump())
        self._binding = PreparedImageBinding.model_validate(binding.model_dump())
        self._attestation = CapabilityAttestation.model_validate(
            attestation.model_dump()
        )
        request = self._action.plan.request
        if (
            request.phase != "BASELINE"
            or self._action.plan.command_id != "PYTEST_FILES_MEASURED_V1"
            or self._binding.plan_ref != plan_ref(self._action.plan)
            or self._binding.execution_id != self._action.execution_id
            or self._binding.tree_ref != self._action.source_tree_ref
            or self._binding.image_uuid != config.image_uuid
            or self._binding.image_digest != request.image_digest
            or self._binding.runner_id != self._attestation.payload.runner_id
            or self._binding.configuration_ref
            != self._attestation.payload.configuration_ref
            or self._binding.configuration_ref
            != confinement_configuration_ref(
                self._action.plan, self._binding.image_uuid, self._action.execution_id
            )
        ):
            raise RunnerError("S_BASELINE_BINDING_DENIED")
        self._config, self._verifier = config, verifier
        self._receipt_key, self._receipt_key_id = receipt_key, receipt_key_id
        self._live, self._transport = allow_live, transport

    @property
    def origin(self) -> Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]:
        return "TEST_TRANSPORT" if self._transport is not None else "REMOTE_SERVICE"

    @property
    def action(self) -> BaselineAction:
        return self._action

    @property
    def binding(self) -> PreparedImageBinding:
        return self._binding

    @property
    def attestation(self) -> CapabilityAttestation:
        return self._attestation

    @property
    def verifier(self) -> CapabilityVerifier:
        return self._verifier

    @property
    def receipt_key_id(self) -> str:
        return self._receipt_key_id

    @property
    def receipt_public_key(self) -> Ed25519PublicKey:
        return self._receipt_key.public_key()

    @property
    def run_id(self) -> UUID:
        return self._action.plan.request.run_id

    def receipt_verifier(self) -> ReceiptVerifier:
        return ReceiptVerifier(
            {self.receipt_key_id: self.receipt_public_key}, origin=self.origin
        )

    def validate_run(self, run: RunState) -> None:
        request = self._action.plan.request
        if (
            run.current_stage != Stage.REPRODUCING
            or run.cancellation_requested
            or request.run_id != run.run_id
            or request.repository_id != run.repository_id
            or request.source_commit != run.source_commit
            or request.source_manifest_ref != run.source_manifest_ref
            or request.reproduction_test_ref != run.reproduction_test_ref
        ):
            raise RunnerError("S_BASELINE_BINDING_DENIED")

    @contextmanager
    def open(
        self, store: Store, lease: Lease, owner: UUID, *, expected_version: int
    ) -> Iterator[tuple[DurableConfinedBackend, BaselineProducerJournal]]:
        """Bind an internal client to the version of the one-use reservation."""
        journal = BaselineProducerJournal(
            store,
            lease,
            owner,
            self.binding,
            receipt_key_id=self.receipt_key_id,
            receipt_public_key=self.receipt_public_key,
            origin=self.origin,
            expected_version=expected_version,
        )
        with DurableConfinedBackend(
            self._config,
            self.binding,
            self.attestation,
            self.verifier,
            receipt_key=self._receipt_key,
            receipt_key_id=self.receipt_key_id,
            journal=journal,
            allow_privileged_setup=True,
            allow_live=self._live,
            transport=self._transport,
        ) as backend:
            yield backend, journal
