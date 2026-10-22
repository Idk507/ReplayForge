"""Explicit trusted-worker receipt production from owned provider observations.

No key discovery/generation, arbitrary signing endpoint, host workload execution,
or automatic dispatch. Durable dispatch fencing remains the caller's obligation.
"""

import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.execution import TestCommandPlan
from packages.contracts.sandbox import RunnerError
from services.runner.admission import CapabilityAttestation, CapabilityVerifier
from services.runner.prepared import PreparedImageBinding, PreparedNebiusBackend
from services.runner.receipts import (
    ExecutionReceipt,
    SignedReceipt,
    SignedReplayReceipt,
    canonical,
    plan_ref,
)
from services.runner.remote import SandboxConfig
from services.runner.responses import ProcessState
from services.runner.workspace import WorkspaceMeasurement, archive_identity


class ProducedReceipt(BaseModel):
    """Private signed facts, not a complete verdict or publication authority."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )
    operation_id: UUID
    source: WorkspaceMeasurement
    final: WorkspaceMeasurement
    signed: SignedReceipt | SignedReplayReceipt
    junit: bytes = Field(min_length=1, max_length=65536, repr=False)
    can_authorize_verification: Literal[False] = False


class MeasuredPreparedBackend(PreparedNebiusBackend):
    """Single owned execution with independently read pre/post workspace bytes.

    The configured signing key lives in the trusted worker only. Image digest,
    source/test preparation and capability assessment still require a trusted
    materializer/operator; inspecting an image UUID does not establish those.
    A process restart cannot restore the pre-measurement: use the existing
    durable dispatcher to prohibit resubmission and fail inconclusive instead.
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
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if (
            not isinstance(receipt_key, Ed25519PrivateKey)
            or not isinstance(receipt_key_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", receipt_key_id)
        ):
            raise RunnerError("S_RECEIPT_KEY_INVALID")
        super().__init__(
            config,
            binding,
            attestation,
            verifier,
            allow_live=allow_live,
            transport=transport,
        )
        self._receipt_key = receipt_key
        self._receipt_key_id = receipt_key_id
        self._measured_plan: TestCommandPlan | None = None
        self._source: WorkspaceMeasurement | None = None
        self._receipt: ProducedReceipt | None = None

    def _measure(self, image: UUID) -> WorkspaceMeasurement:
        content = self._http._request(
            "GET", f"v1/inspect/{image}/archive?path=/workspace/repo", 200
        )
        reference, count, size = archive_identity(content)
        return WorkspaceMeasurement(
            image_uuid=image,
            tree_ref=reference,
            files=count,
            bytes_measured=size,
            evidence_origin=self.origin,
        )

    def submit(
        self, plan: TestCommandPlan, execution_id: UUID, expected_tree_ref: str
    ) -> UUID:
        """Measure only after admission; never submit a mismatched workspace."""
        plan = self._validate_submission(plan, execution_id, expected_tree_ref)
        if plan.command_id != "PYTEST_FILES_MEASURED_V1":
            raise RunnerError("S_MEASURED_COMMAND_REQUIRED")
        source = self._measure(self._binding.image_uuid)
        if source.tree_ref != expected_tree_ref:
            raise RunnerError("S_MEASURED_TREE_MISMATCH")
        operation = super().submit(plan, execution_id, expected_tree_ref)
        self._measured_plan = plan
        self._source = source
        return operation

    def collect_receipt(self, operation_id: UUID) -> ProducedReceipt:
        """Read actual process/report/workspace facts and sign their exact values.

        Failed tests or workspace mutation are signed honestly for the verifier
        to reject. Pending/missing/invalid observations produce no receipt. There
        is no caller-provided report, exit code or observed tree to launder.
        """
        self._owned(operation_id)
        if self._receipt is not None:
            return self._receipt
        plan, source = self._measured_plan, self._source
        if plan is None or source is None:
            raise RunnerError("S_PREMEASUREMENT_MISSING")
        operation = self.inspect(operation_id)
        if operation.status not in {"SUCCESS", "FAILED"}:
            raise RunnerError("S_REPORT_NOT_READY")
        image = operation.result_image_uuid
        if image is None or image == source.image_uuid:
            raise RunnerError("S_REPORT_IMAGE_INVALID")
        process = operation.metadata.get("result")
        if not isinstance(process, dict):
            raise RunnerError("S_SCHEMA_INVALID")
        try:
            state = ProcessState.model_validate(process.get("state"))
            # Validate bounded receipt facts before report/measurement I/O.
            payload = ExecutionReceipt(
                execution_id=self._binding.execution_id,
                run_id=plan.request.run_id,
                plan_ref=plan_ref(plan),
                source_tree_ref=source.tree_ref,
                final_tree_ref=source.tree_ref,
                junit_ref="sha256:" + "0" * 64,
                origin=self.origin,
                observed_at=datetime.now(UTC),
                exit_code=state.exit_code,
                terminal=True,
                timed_out=state.timed_out,
                signal=state.signal,
            )
        except ValidationError:
            raise RunnerError("S_SCHEMA_INVALID") from None
        report = self._http._request(
            "GET",
            f"v1/inspect/{image}/download?path=/tmp/replayforge-result.xml&text=true",
            200,
        )
        if not report:
            raise RunnerError("S_REPORT_EMPTY")
        final = self._measure(image)
        payload = ExecutionReceipt.model_validate(
            {
                **payload.model_dump(),
                "final_tree_ref": final.tree_ref,
                "junit_ref": "sha256:" + sha256(report).hexdigest(),
                "observed_at": datetime.now(UTC),
            }
        )
        envelope = (
            SignedReceipt if plan.request.phase == "BASELINE" else SignedReplayReceipt
        )
        unsigned = envelope(
            key_id=self._receipt_key_id, payload=payload, signature="0" * 128
        )
        signed = envelope.model_validate(
            {
                **unsigned.model_dump(),
                "signature": self._receipt_key.sign(
                    canonical(unsigned, unsigned=True)
                ).hex(),
            }
        )
        self._receipt = ProducedReceipt(
            operation_id=operation_id,
            source=source,
            final=final,
            signed=signed,
            junit=report,
        )
        return self._receipt
