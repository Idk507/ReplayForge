"""Measured runner with exact-template admission and protected child collection.

This supplies no capability attestation. An independent assessor must bind actual
OCI/environment provenance, controls and collector trust before live dispatch.
"""

from typing import Literal
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.execution import TestCommandPlan
from packages.contracts.run import ArtifactID
from packages.contracts.sandbox import RunnerError
from services.persistence.baseline_producer import BaselineProducerJournal
from services.persistence.producer import ProducerJournal
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.confinement import (
    confined_test_request,
    confinement_configuration_ref,
)
from services.runner.durable_producer import DurableMeasuredBackend
from services.runner.prepared import PreparedImageBinding
from services.runner.producer import MeasuredPreparedBackend
from services.runner.receipts import plan_ref
from services.runner.remote import SandboxConfig
from services.runner.responses import Operation, ProcessState


class _Closed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class _Process(_Closed):
    CapEff: Literal["0000000000000000"]
    NoNewPrivs: Literal["1"]
    Seccomp: Literal["2"]


class _Observation(_Closed):
    uid: int = Field(strict=True, ge=1000, le=1000)
    gid: int = Field(strict=True, ge=1000, le=1000)
    process: _Process
    root_mount_read_only: bool = Field(strict=True)
    tmp_bytes: int = Field(strict=True, ge=1, le=16777216)
    workspace_bytes: int = Field(strict=True, ge=1, le=16777216)
    cgroup: dict[str, str]


class _ChildResult(_Closed):
    run_id: UUID
    execution_id: UUID
    plan_ref: ArtifactID
    argv: tuple[str, ...] = Field(max_length=32)
    env: dict[str, str]
    confinement: _Observation
    exit_code: int = Field(strict=True, ge=-255, le=255)
    timed_out: bool = Field(strict=True)
    output_limited: bool = Field(strict=True)


class ConfinedMeasuredBackend(MeasuredPreparedBackend):
    """Explicit confined alternative; ordinary prepared backends stay unchanged.

    Set ``allow_privileged_setup`` explicitly at construction. The root
    supervisor is fixed code in the remote guest only;
    arbitrary commands or capability profiles cannot be supplied through it.
    Default construction never grants setup permission or performs I/O.
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
        allow_privileged_setup: bool = False,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._allow_privileged_setup = allow_privileged_setup is True
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

    def _validate_submission(
        self, plan: TestCommandPlan, execution_id: UUID, expected_tree_ref: str
    ) -> TestCommandPlan:
        if self._allow_privileged_setup is not True:
            raise RunnerError("CONFINEMENT_SETUP_NOT_AUTHORIZED")
        expected = confinement_configuration_ref(
            plan, self._binding.image_uuid, execution_id
        )
        if expected != self._binding.configuration_ref:
            raise RunnerError("CONFINEMENT_CONFIGURATION_MISMATCH")
        return super()._validate_submission(plan, execution_id, expected_tree_ref)

    def _spawn_payload(self, plan: TestCommandPlan) -> dict[str, object]:
        return confined_test_request(
            plan,
            self._binding.image_uuid,
            self._binding.execution_id,
            allow_privileged_setup=self._allow_privileged_setup,
        )

    def _process_state(
        self, operation: Operation, plan: TestCommandPlan
    ) -> ProcessState:
        outer = super()._process_state(operation, plan)
        if outer.timed_out or outer.signal is not None:
            raise RunnerError("CONFINEMENT_SUPERVISOR_INTERRUPTED")
        image = operation.result_image_uuid
        if image is None:
            raise RunnerError("S_REPORT_IMAGE_INVALID")
        try:
            child = _ChildResult.model_validate_json(
                self._http._request(
                    "GET",
                    f"v1/inspect/{image}/download?path=/tmp/replayforge-execution.json&text=true",
                    200,
                )
            )
        except ValidationError:
            raise RunnerError("CONFINEMENT_CHILD_REPORT_INVALID") from None
        observed = child.confinement
        expected_outer = (
            124
            if child.timed_out or child.output_limited
            else child.exit_code
            if child.exit_code >= 0
            else 128 - child.exit_code
        )
        if (
            child.run_id != plan.request.run_id
            or child.execution_id != self._binding.execution_id
            or child.plan_ref != plan_ref(plan)
            or child.argv != plan.argv
            or child.env != dict(plan.env)
            or not observed.root_mount_read_only
            or observed.cgroup
            != {
                "cpu.max": "100000 100000",
                "memory.max": "134217728",
                "memory.swap.max": "0",
                "pids.max": "16",
            }
            or expected_outer != outer.exit_code
            or child.output_limited
        ):
            raise RunnerError("CONFINEMENT_CHILD_BINDING_DENIED")
        return ProcessState(
            exit_code=child.exit_code,
            signal=-child.exit_code if child.exit_code < 0 else None,
            timed_out=child.timed_out,
        )


class DurableConfinedBackend(DurableMeasuredBackend, ConfinedMeasuredBackend):
    """Fenced preparation/recovery combined with fixed confinement collection.

    Cooperative parent initialization applies the existing journal/key checks
    and the confined collector; no separate operation attachment path is added.
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
        allow_privileged_setup: bool = False,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            config,
            binding,
            attestation,
            verifier,
            receipt_key=receipt_key,
            receipt_key_id=receipt_key_id,
            journal=journal,
            allow_live=allow_live,
            transport=transport,
        )
        self._allow_privileged_setup = allow_privileged_setup is True
