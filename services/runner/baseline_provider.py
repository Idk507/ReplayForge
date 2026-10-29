"""Concrete read-only provider for one independently prepared Nebius workspace."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

import httpx

from packages.contracts.run import RunState, Stage
from packages.contracts.sandbox import RunnerError
from services.orchestrator.baseline_worker import PreparedBaseline
from services.orchestrator.reproduction import ReproductionArtifact
from services.persistence.baseline import BaselineAction
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.prepared import PreparedImageBinding, PreparedNebiusBackend
from services.runner.receipts import plan_ref
from services.runner.remote import SandboxConfig


class NebiusBaselineProvider:
    """Resolve immutable operator inputs; never fabricate preparation or assessment.

    Construction and resolution perform no provider request. Live use requires
    explicit permission here and in the worker/dispatcher. Transport injection
    remains TEST_TRANSPORT. Context exit closes the REST client, not compute.
    """

    def __init__(
        self,
        config: SandboxConfig,
        action: BaselineAction,
        binding: PreparedImageBinding,
        attestation: CapabilityAttestation,
        verifier: CapabilityVerifier,
        *,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if transport is None and allow_live is not True:
            raise RunnerError("S_LIVE_NOT_AUTHORIZED")
        self._action = BaselineAction.model_validate(action.model_dump())
        self._binding = PreparedImageBinding.model_validate(binding.model_dump())
        self._attestation = CapabilityAttestation.model_validate(
            attestation.model_dump()
        )
        if self._action.plan.command_id == "PYTEST_FILES_MEASURED_V1":
            # This provider creates PreparedNebiusBackend, which has neither a
            # durable premeasurement nor the confined measured collector.
            raise RunnerError("S_BASELINE_MEASURED_BACKEND_REQUIRED")
        if (
            action.plan.request.phase != "BASELINE"
            or action.plan.command_id
            not in {"PYTEST_FILES_SAVED_JUNIT_V1", "PYTEST_FILES_MEASURED_V1"}
            or binding.plan_ref != plan_ref(action.plan)
            or binding.execution_id != action.execution_id
            or binding.tree_ref != action.source_tree_ref
            or binding.image_uuid != config.image_uuid
            or binding.image_digest != action.plan.request.image_digest
            or binding.runner_id != attestation.payload.runner_id
            or binding.configuration_ref != attestation.payload.configuration_ref
        ):
            raise RunnerError("S_BASELINE_BINDING_DENIED")
        self._config, self._verifier = config, verifier
        self._live, self._transport = allow_live, transport

    @property
    def origin(self) -> Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]:
        return "TEST_TRANSPORT" if self._transport is not None else "REMOTE_SERVICE"

    @property
    def run_id(self) -> UUID:
        return self._action.plan.request.run_id

    @contextmanager
    def resolve(
        self, run: RunState, reproduction: ReproductionArtifact
    ) -> Iterator[PreparedBaseline]:
        request = self._action.plan.request
        if (
            run.current_stage != Stage.REPRODUCING
            or run.cancellation_requested
            or request.run_id != run.run_id
            or request.repository_id != run.repository_id
            or request.source_commit != run.source_commit
            or request.source_manifest_ref != run.source_manifest_ref
            or request.reproduction_test_ref != run.reproduction_test_ref
            or reproduction.artifact_ref() != run.reproduction_test_ref
            or reproduction.context_refs != run.context_refs
            or reproduction.proposal.target_path != request.reproduction_path
        ):
            raise RunnerError("S_BASELINE_BINDING_DENIED")
        with PreparedNebiusBackend(
            self._config,
            self._binding,
            self._attestation,
            self._verifier,
            allow_live=self._live,
            transport=self._transport,
        ) as backend:
            yield PreparedBaseline(
                self._action, self._attestation, self._verifier, backend
            )
