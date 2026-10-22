"""REST backend for an operator-prepared immutable image; no host execution."""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from packages.contracts.execution import TestCommandPlan
from packages.contracts.run import ArtifactID
from packages.contracts.sandbox import RunnerError
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.receipts import plan_ref
from services.runner.remote import NebiusSandboxBackend, SandboxConfig
from services.runner.responses import Created, Operation
from services.runner.rest_plan import build_test_spawn


class PreparedImageBinding(BaseModel):
    """Trusted materializer output, never public/model-provided proof.

    The operator must establish the actual prepared image's OCI identity, tree,
    unchanged test and runtime configuration before constructing this binding.
    A provider UUID or deserialized object does not establish those facts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    plan_ref: ArtifactID
    execution_id: UUID
    tree_ref: ArtifactID
    image_uuid: UUID
    image_digest: ArtifactID
    runner_id: UUID
    configuration_ref: ArtifactID


class PreparedNebiusBackend:
    """Single-submit prepared-workspace port, with explicit owned lifecycle I/O.

    Use through dispatch_replay so its durable claim precedes submission. Direct
    use is a low-level operator API, not durable dispatch authorization. A custom
    HTTP transport is always TEST_TRANSPORT, never live execution evidence.
    """

    def __init__(
        self,
        config: SandboxConfig,
        binding: PreparedImageBinding,
        attestation: CapabilityAttestation,
        verifier: CapabilityVerifier,
        *,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if transport is None and allow_live is not True:
            raise RunnerError("S_LIVE_NOT_AUTHORIZED")
        self._binding = PreparedImageBinding.model_validate(binding.model_dump())
        self._attestation = CapabilityAttestation.model_validate(
            attestation.model_dump()
        )
        if config.image_uuid != self._binding.image_uuid:
            raise RunnerError("S_PREPARED_IMAGE_MISMATCH")
        self._verifier = verifier
        self._origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"] = (
            "TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"
        )
        # Reuse only the fixed-destination, bounded HTTP transport, not its
        # smoke command or owned-operation state. No arbitrary URL is accepted.
        self._http = NebiusSandboxBackend(config, transport=transport)
        self._operation: UUID | None = None
        self._submitted = False
        self._closed = False

    @property
    def origin(self) -> Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]:
        return self._origin

    @property
    def runner_id(self) -> UUID:
        return self._binding.runner_id

    @property
    def configuration_ref(self) -> str:
        return self._binding.configuration_ref

    def __enter__(self) -> Self:
        if self._closed:
            raise RunnerError("S_CLOSED")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release HTTP resources; closing a client does not terminate compute."""
        if not self._closed:
            self._http.close()
            self._closed = True

    def submit(
        self, plan: TestCommandPlan, execution_id: UUID, expected_tree_ref: str
    ) -> UUID:
        """Submit the exact reserved plan once; ambiguous failures stay unknown."""
        if self._closed or self._submitted:
            raise RunnerError("S_CLOSED_OR_ALREADY_SUBMITTED")
        plan = TestCommandPlan.model_validate(plan.model_dump())
        binding = self._binding
        if (
            plan_ref(plan) != binding.plan_ref
            or execution_id != binding.execution_id
            or expected_tree_ref != binding.tree_ref
            or plan.request.image_digest != binding.image_digest
        ):
            raise RunnerError("S_PREPARED_BINDING_DENIED")
        assessment = self._verifier.assess(
            self._attestation, plan, now=datetime.now(UTC)
        )
        if (
            assessment.status != "CAPABILITY_REQUIREMENTS_MET"
            or self._attestation.payload.runner_id != binding.runner_id
            or self._attestation.payload.configuration_ref != binding.configuration_ref
        ):
            raise RunnerError("S_PREPARED_ADMISSION_DENIED")
        payload = build_test_spawn(plan, binding.image_uuid)
        self._submitted = True
        try:
            body = self._http._request("POST", "v1/instances", 201, payload)
            self._operation = Created.model_validate_json(body).uuid
        except (RunnerError, ValidationError) as error:
            if isinstance(error, RunnerError) and str(error) in {
                "S_AUTH",
                "S_REDIRECT_DENIED",
            }:
                raise
            # Match the dispatch port's safe uncertain-submission convention.
            raise TimeoutError("S_SUBMISSION_UNKNOWN") from None
        return self._operation

    def _owned(self, operation_id: UUID) -> None:
        if self._closed or self._operation is None or self._operation != operation_id:
            raise RunnerError("S_OPERATION_NOT_OWNED")

    def inspect(self, operation_id: UUID) -> Operation:
        """One bounded status read; operation SUCCESS is not test success."""
        self._owned(operation_id)
        try:
            operation = Operation.model_validate_json(
                self._http._request("GET", f"v1/operations/{operation_id}", 200)
            )
        except ValidationError:
            raise RunnerError("S_SCHEMA_INVALID") from None
        if (
            operation.uuid != operation_id
            or operation.image_uuid != self._binding.image_uuid
        ):
            raise RunnerError("S_CORRELATION_FAILED")
        return operation

    def terminate(self, operation_id: UUID) -> None:
        """Request cancellation only; caller must inspect to confirm terminality."""
        self._owned(operation_id)
        self._http._request("DELETE", f"v1/operations/{operation_id}", 202)
