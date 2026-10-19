"""Single-read reconciliation of a durably known operation; no remote writes."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from packages.contracts.run import ArtifactID
from packages.contracts.sandbox import RunnerError
from services.persistence.baseline import BaselineJournal
from services.persistence.operations import OperationJournal, Provider
from services.persistence.store import Lease, Store, StoreError
from services.runner.remote import NebiusSandboxBackend, SandboxConfig
from services.runner.responses import Operation


class RecoveryResult(BaseModel):
    """Safe lifecycle metadata, never a signed test result or cleanup proof."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["PENDING", "COMPUTE_TERMINAL", "ALREADY_TERMINAL"]
    observed_at: AwareDatetime
    trace_id: UUID
    execution_id: UUID
    operation_id: UUID
    action_ref: ArtifactID
    provider_status: (
        Literal["PENDING", "ASSIGNED", "EXECUTING", "SUCCESS", "FAILED", "CANCELLED"]
        | None
    )
    evidence_origin: Literal["TEST_TRANSPORT", "UNVERIFIED_ADAPTER_INPUT"]
    policy_decision: Literal["ALLOW_KNOWN_OPERATION_READ_ONLY"] = (
        "ALLOW_KNOWN_OPERATION_READ_ONLY"
    )
    retryable: Literal[False] = False
    error_class: None = None
    artifacts: tuple[ArtifactID, ...]
    can_authorize_execution: Literal[False] = False


def recover_baseline_operation(
    store: Store,
    lease: Lease,
    owner: UUID,
    execution_id: UUID,
    config: SandboxConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> RecoveryResult:
    """Inspect once using a fenced, owner-scoped persisted operation identity.

    Unknown submission cannot be searched or resubmitted. Caller scheduling
    controls subsequent reads; transport/auth/schema faults propagate safely
    without retries, cancellation, refunds, test acceptance or stage changes.
    A custom transport is always TEST_TRANSPORT, never live provider evidence.
    """
    claimed_owner, run = store.claimed_run(lease)
    if claimed_owner != owner:
        raise StoreError("NOT_FOUND")
    action = BaselineJournal(store).action(execution_id, owner)
    history = OperationJournal(store).history(execution_id, owner)
    provider: Provider = "TEST_TRANSPORT" if transport is not None else "NEBIUS_CONTREE"
    if (
        action.plan.request.run_id != run.run_id
        or not history
        or history[-1].provider != provider
        or history[-1].operation_id is None
    ):
        raise StoreError("OPERATION_RECOVERY_DENIED")
    previous = history[-1]
    operation_id = previous.operation_id
    assert operation_id is not None  # Checked before constructing an HTTP client.
    operation: Operation | None = None
    if previous.phase != "COMPUTE_TERMINAL":
        # Reuse the credential/destination/size/timeout guards of the existing
        # REST client. Do not set its owned-submission state or call write APIs.
        backend = NebiusSandboxBackend(config, transport=transport)
        try:
            body = backend._request("GET", f"v1/operations/{operation_id}", 200)
            try:
                operation = Operation.model_validate_json(body)
            except ValidationError:
                raise RunnerError("S_SCHEMA_INVALID") from None
            if (
                operation.uuid != operation_id
                or operation.image_uuid != backend.config.image_uuid
            ):
                raise RunnerError("S_CORRELATION_FAILED")
        finally:
            backend.close()
        # Fence again after I/O, even for pending observations. A stale worker
        # cannot report success; a terminal append rechecks inside its transaction.
        after_owner, after = store.claimed_run(lease)
        if after_owner != owner or after.version != run.version:
            raise StoreError("STALE_VERSION")
        if operation.status in {"SUCCESS", "FAILED", "CANCELLED"}:
            OperationJournal(store).record(
                lease,
                owner,
                execution_id,
                provider=provider,
                phase="COMPUTE_TERMINAL",
                expected_version=run.version,
                operation_id=operation_id,
            )
    return RecoveryResult(
        status="ALREADY_TERMINAL"
        if operation is None
        else (
            "COMPUTE_TERMINAL"
            if operation.status in {"SUCCESS", "FAILED", "CANCELLED"}
            else "PENDING"
        ),
        observed_at=datetime.now(UTC),
        trace_id=run.trace_id,
        execution_id=execution_id,
        operation_id=operation_id,
        action_ref=previous.action_ref,
        provider_status=None if operation is None else operation.status,
        evidence_origin="TEST_TRANSPORT"
        if transport is not None
        else "UNVERIFIED_ADAPTER_INPUT",
        artifacts=(previous.action_ref,),
    )
