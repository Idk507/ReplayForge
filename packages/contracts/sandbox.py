"""M0 sandbox probe contracts; these cannot authorize repository execution."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    """Immutable public envelope with strict, closed schema validation."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class ProbeStatus(StrEnum):
    """Connectivity/isolation observations, distinct from repair verdicts."""

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    NOT_VERIFIED = "NOT_VERIFIED"


class RunnerError(Exception):
    """Sanitized stable code; raw provider exceptions must not cross this port."""


class GuestObservation(Contract):
    """Only allowlisted synthetic data may be exposed by the smoke report."""

    python_version: str = Field(pattern=r"^Python 3\.[0-9]{1,2}\.[0-9]{1,3}$")
    uid: int = Field(strict=True, ge=0)
    gid: int = Field(strict=True, ge=0)
    interfaces: list[str] = Field(max_length=16)
    docker_socket_present: bool = Field(strict=True)
    credential_env_present: bool = Field(strict=True)


class ArtifactRef(Contract):
    """Digest of the canonical observation embedded in this report."""

    artifact_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class SandboxSmokeReport(Contract):
    """Safe tool envelope. A terminal compute state is not a deletion guarantee."""

    schema_version: Literal["1.0"] = "1.0"
    status: ProbeStatus
    evidence_origin: Literal["NOT_RUN", "TEST_TRANSPORT", "REMOTE_SERVICE"] = "NOT_RUN"
    observed_at: datetime
    trace_id: UUID
    policy_decision: Literal["DENY_LIVE_NOT_AUTHORIZED", "ALLOW_M0_SYNTHETIC_PROBE"]
    image_uuid: UUID
    operation_id: UUID | None = None
    compute_terminal_confirmed: bool = False
    retryable: Literal[False] = False
    error_class: str | None = None
    cleanup_error_class: str | None = None
    python_version: str | None = None
    observation: GuestObservation | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    limitations: tuple[str, ...] = (
        "Synthetic M0 probe only; arbitrary repository execution is disabled.",
        "Image UUID is provider identity, not a verified OCI SHA256 digest.",
        "Terminal compute does not prove deletion of images or provider logs.",
        "CPU, memory, PID, read-only root and retention controls remain unverified.",
    )
