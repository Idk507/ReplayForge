"""Bounded M0 lifecycle and evidence checks, never a repair verifier."""

import base64
import binascii
import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from packages.contracts.sandbox import (
    ArtifactRef,
    GuestObservation,
    ProbeStatus,
    RunnerError,
    SandboxSmokeReport,
)
from services.runner.interface import SandboxBackend
from services.runner.remote import NebiusSandboxBackend, SandboxConfig, probe_request
from services.runner.responses import Operation, ProcessResult, Stream

TERMINAL = frozenset({"SUCCESS", "FAILED", "CANCELLED"})


def _decode(stream: Stream) -> str:
    """Decode bounded output; do not accept truncation or invalid base64."""
    if stream.truncated:
        raise RunnerError("S_OUTPUT_INVALID")
    try:
        if stream.encoding == "base64":
            return base64.b64decode(stream.value, validate=True).decode("utf-8")
        stream.value.encode("ascii" if stream.encoding == "ascii" else "utf-8")
        return stream.value
    except (ValueError, UnicodeError, binascii.Error):
        raise RunnerError("S_OUTPUT_INVALID") from None


def _observation(operation: Operation, image_uuid: UUID) -> GuestObservation:
    """Validate original request, process result and safe guest invariants."""
    if operation.status != "SUCCESS":
        raise RunnerError("S_OPERATION_FAILED")
    expected = probe_request(image_uuid)
    if (
        operation.result_image_uuid is not None
        or any(
            json.dumps(operation.metadata.get(key), sort_keys=True)
            != json.dumps(value, sort_keys=True)
            for key, value in expected.items()
        )
        or operation.metadata.get("files") not in (None, {})
    ):
        raise RunnerError("S_POLICY_DENIED")
    try:
        result = ProcessResult.model_validate(operation.metadata.get("result"))
    except ValidationError:
        raise RunnerError("S_SCHEMA_INVALID") from None
    state = result.state
    if state.exit_code != 0 or state.timed_out or state.signal not in (None, -1):
        raise RunnerError("S_PROCESS_FAILED")
    if _decode(result.stderr):
        raise RunnerError("S_OUTPUT_INVALID")
    try:
        observed = GuestObservation.model_validate_json(_decode(result.stdout))
    except ValidationError:
        raise RunnerError("S_OUTPUT_INVALID") from None
    if (
        observed.uid != 1000
        or observed.gid != 1000
        or observed.interfaces != ["lo"]
        or observed.docker_socket_present
        or observed.credential_env_present
    ):
        raise RunnerError("S_ISOLATION_FAILED")
    return observed


def _wait(
    backend: SandboxBackend,
    operation_id: UUID,
    *,
    polls: int,
    deadline: float,
    sleep: Callable[[float], None],
) -> Operation:
    """Both poll count and wall time bound waits; no new workload is submitted."""
    for _ in range(polls):
        if time.monotonic() >= deadline:
            raise RunnerError("S_TIMEOUT")
        operation = backend.inspect(operation_id)
        if operation.status in TERMINAL:
            return operation
        sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    raise RunnerError("S_TIMEOUT") 


def run_smoke(
    config: SandboxConfig,
    *,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SandboxSmokeReport:
    """Execute one fixed remote probe only after explicit operator opt-in.

    Known work is cancelled on polling failure with a separate bounded wait for
    terminal status. Unknown POST outcomes cannot be safely cancelled or retried.
    No provider error body, raw stdout/stderr or credential enters the report.
    """
    trace_id = uuid4()
    if not allow_live:
        return SandboxSmokeReport(
            status=ProbeStatus.NOT_RUN,
            observed_at=datetime.now(UTC),
            trace_id=trace_id,
            policy_decision="DENY_LIVE_NOT_AUTHORIZED",
            image_uuid=config.image_uuid,
        )
    backend = NebiusSandboxBackend(config, transport=transport)
    operation_id = None
    terminal = False
    error = None
    cleanup_error = None
    observation = None
    try:
        operation_id = backend.start_probe()
        operation = _wait(
            backend, operation_id, polls=20, deadline=time.monotonic() + 45, sleep=sleep
        )
        terminal = True
        observation = _observation(operation, config.image_uuid)
    except RunnerError as failure:
        error = str(failure)
    finally:
        try:
            if operation_id is not None and not terminal:
                try:
                    backend.terminate(operation_id)
                except RunnerError as failure:
                    cleanup_error = str(failure)
                # A cancellation request may race with normal completion or
                # lose its acknowledgement. Still inspect the owned operation.
                try:
                    _wait(
                        backend,
                        operation_id,
                        polls=5,
                        deadline=time.monotonic() + 10,
                        sleep=sleep,
                    )
                    terminal = True
                except RunnerError as failure:
                    cleanup_error = str(failure)
        finally:
            backend.close()
    artifacts: tuple[ArtifactRef, ...] = ()
    if observation is not None:
        canonical = json.dumps(
            observation.model_dump(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        artifacts = (
            ArtifactRef(
                artifact_id=f"sha256:{digest}", sha256=digest, size_bytes=len(canonical)
            ),
        )
    return SandboxSmokeReport(
        status=ProbeStatus.PASSED if error is None else ProbeStatus.NOT_VERIFIED,
        evidence_origin="TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE",
        observed_at=datetime.now(UTC),
        trace_id=trace_id,
        policy_decision="ALLOW_M0_SYNTHETIC_PROBE",
        image_uuid=config.image_uuid,
        operation_id=operation_id,
        compute_terminal_confirmed=terminal,
        error_class=error,
        cleanup_error_class=cleanup_error,
        python_version=observation.python_version if observation else None,
        observation=observation,
        artifacts=artifacts,
    )
