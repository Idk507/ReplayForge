"""Read-only provider observation; no signing, submission or host execution."""

import base64
import binascii
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Self
from uuid import UUID

import httpx
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
from packages.contracts.sandbox import RunnerError
from services.runner.receipts import plan_ref
from services.runner.remote import NebiusSandboxBackend, SandboxConfig
from services.runner.responses import Operation, ProcessState


class ReplayObservation(BaseModel):
    """Private exact report bytes and process facts, never a verified receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["PENDING", "REPORT_COLLECTED", "TERMINAL_NO_REPORT"]
    run_id: UUID
    execution_id: UUID
    operation_id: UUID
    image_uuid: UUID
    result_image_uuid: UUID | None
    plan_ref: ArtifactID
    capability_ref: ArtifactID
    provider_status: Literal[
        "PENDING", "ASSIGNED", "EXECUTING", "SUCCESS", "FAILED", "CANCELLED"
    ]
    process_state: ProcessState | None
    junit_b64: str | None = Field(default=None, max_length=87384, repr=False)
    junit_ref: ArtifactID | None = None
    evidence_origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    observed_at: AwareDatetime
    trace_id: UUID
    policy_decision: Literal["ALLOW_KNOWN_REPLAY_COLLECTION"] = (
        "ALLOW_KNOWN_REPLAY_COLLECTION"
    )
    artifacts: tuple[ArtifactID, ...] = Field(max_length=2)
    retryable: Literal[False] = False
    error_class: None = None
    can_authorize_execution: Literal[False] = False
    can_authorize_verification: Literal[False] = False

    def junit_bytes(self) -> bytes | None:
        if self.junit_b64 is None:
            return None
        try:
            return base64.b64decode(self.junit_b64, validate=True)
        except (ValueError, binascii.Error):
            raise RunnerError("S_REPORT_ENCODING") from None

    @model_validator(mode="after")
    def report_binding(self) -> Self:
        terminal = self.provider_status in {"SUCCESS", "FAILED", "CANCELLED"}
        if (self.status == "PENDING") == terminal:
            raise ValueError("OBSERVATION_STATUS_INVALID")
        if self.provider_status in {"SUCCESS", "FAILED"} and self.process_state is None:
            raise ValueError("OBSERVATION_PROCESS_INVALID")
        try:
            report = self.junit_bytes()
        except RunnerError:
            raise ValueError("OBSERVATION_REPORT_INVALID") from None
        if self.status == "REPORT_COLLECTED":
            if (
                self.provider_status not in {"SUCCESS", "FAILED"}
                or self.process_state is None
                or self.result_image_uuid in {None, self.image_uuid}
                or report is None
                or not 1 <= len(report) <= 65536
                or self.junit_ref != "sha256:" + sha256(report).hexdigest()
                or self.artifacts != (self.plan_ref, self.junit_ref)
            ):
                raise ValueError("OBSERVATION_REPORT_INVALID")
        elif (
            report is not None
            or self.junit_ref is not None
            or self.artifacts != (self.plan_ref,)
        ):
            raise ValueError("OBSERVATION_REPORT_INVALID")
        return self


def read_replay_observation(
    config: SandboxConfig,
    plan: TestCommandPlan,
    execution_id: UUID,
    operation_id: UUID,
    capability_ref: str,
    *,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> ReplayObservation:
    """At most two GETs; identities must come from the durable controller.

    This low-level trusted adapter is not an exposed model/browser tool. The
    source image UUID is an operator binding, not proof of an OCI digest.
    """
    if transport is None and allow_live is not True:
        raise RunnerError("S_LIVE_NOT_AUTHORIZED")
    plan = TestCommandPlan.model_validate(plan.model_dump())
    if plan.command_id not in {
        "PYTEST_FILES_SAVED_JUNIT_V1",
        "PYTEST_FILES_MEASURED_V1",
    }:
        raise RunnerError("S_REPORT_EXPORT_DISABLED")
    if not isinstance(operation_id, UUID) or not isinstance(execution_id, UUID):
        raise RunnerError("S_CORRELATION_FAILED")
    backend = NebiusSandboxBackend(config, transport=transport)
    try:
        try:
            operation = Operation.model_validate_json(
                backend._request("GET", f"v1/operations/{operation_id}", 200)
            )
            if (
                operation.uuid != operation_id
                or operation.image_uuid != config.image_uuid
            ):
                raise RunnerError("S_CORRELATION_FAILED")
            terminal = operation.status in {"SUCCESS", "FAILED", "CANCELLED"}
            state = None
            if operation.status in {"SUCCESS", "FAILED"}:
                process = operation.metadata.get("result")
                if not isinstance(process, dict):
                    raise RunnerError("S_SCHEMA_INVALID")
                state = ProcessState.model_validate(process.get("state"))
        except ValidationError:
            raise RunnerError("S_SCHEMA_INVALID") from None
        report = None
        if (
            operation.status in {"SUCCESS", "FAILED"}
            and operation.result_image_uuid is not None
        ):
            if operation.result_image_uuid == config.image_uuid:
                raise RunnerError("S_REPORT_IMAGE_INVALID")
            report = backend._request(
                "GET",
                f"v1/inspect/{operation.result_image_uuid}/download"
                "?path=/tmp/replayforge-result.xml&text=true",
                200,
            )
            if not report:
                raise RunnerError("S_REPORT_EMPTY")
        report_ref = (
            "sha256:" + sha256(report).hexdigest() if report is not None else None
        )
        return ReplayObservation(
            status="REPORT_COLLECTED"
            if report is not None
            else ("TERMINAL_NO_REPORT" if terminal else "PENDING"),
            run_id=plan.request.run_id,
            execution_id=execution_id,
            operation_id=operation_id,
            image_uuid=operation.image_uuid,
            result_image_uuid=operation.result_image_uuid,
            plan_ref=plan_ref(plan),
            capability_ref=capability_ref,
            provider_status=operation.status,
            process_state=state,
            junit_b64=base64.b64encode(report).decode("ascii")
            if report is not None
            else None,
            junit_ref=report_ref,
            evidence_origin="TEST_TRANSPORT"
            if transport is not None
            else "REMOTE_SERVICE",
            observed_at=datetime.now(UTC),
            trace_id=plan.trace_id,
            artifacts=(plan_ref(plan), report_ref) if report_ref else (plan_ref(plan),),
        )
    finally:
        backend.close()
