"""Trusted public-key receipt verification; no signing keys or runner execution."""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.execution import TestCommandPlan
from packages.contracts.run import ArtifactID
from services.runner.baseline import (
    BaselineInspection,
    FailureExpectation,
    inspect_baseline,
)


class ReceiptDenied(Exception):
    """Safe stable error without signatures, keys or workload details."""


class ExecutionReceipt(BaseModel):
    """Claims signed by a trusted collector outside the untrusted workload."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    execution_id: UUID
    run_id: UUID
    plan_ref: ArtifactID
    source_tree_ref: ArtifactID
    final_tree_ref: ArtifactID
    junit_ref: ArtifactID
    origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    observed_at: AwareDatetime
    exit_code: int = Field(strict=True, ge=-255, le=255)
    terminal: bool = Field(strict=True)
    timed_out: bool = Field(strict=True)
    signal: int | None = Field(default=None, strict=True, ge=1, le=255)


class SignedReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    algorithm: Literal["Ed25519"] = "Ed25519"
    purpose: Literal["replayforge.baseline-receipt.v1"] = (
        "replayforge.baseline-receipt.v1"
    )
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    payload: ExecutionReceipt
    signature: str = Field(pattern=r"^[a-f0-9]{128}$", repr=False)


def canonical(model: BaseModel, *, unsigned: bool = False) -> bytes:
    """Versioned canonical JSON; signature binds algorithm and key ID too."""
    data = model.model_dump(mode="json")
    if unsigned:
        data.pop("signature", None)
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def plan_ref(plan: TestCommandPlan) -> str:
    plan = TestCommandPlan.model_validate(plan.model_dump())
    return "sha256:" + hashlib.sha256(canonical(plan)).hexdigest()


class VerifiedReceipt(BaseModel):
    """Signature/correlation verified; not runner capability or repair proof."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    receipt_ref: ArtifactID
    execution_id: UUID
    junit_ref: ArtifactID
    origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    can_authorize_patching: Literal[False] = False


class SignedBaselineInspection(BaseModel):
    """Correlated signed report, still no runtime capability/patch authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    status: Literal["MATCHED_SIGNED_REPORT", "REJECTED_SIGNED_REPORT"]
    receipt: VerifiedReceipt
    inspection: BaselineInspection
    can_authorize_patching: Literal[False] = False


class ReceiptVerifier:
    """Explicit trusted key registry; no discovery, persistence or fallback.

    Execution ID/tree/report expectations must come from a durable trusted action
    record. This stateless verifier does not consume or reconcile that record.
    """

    def __init__(
        self,
        keys: Mapping[str, Ed25519PublicKey],
        *,
        origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"],
    ) -> None:
        if (
            origin not in {"TEST_TRANSPORT", "REMOTE_SERVICE"}
            or not 1 <= len(keys) <= 16
        ):
            raise ReceiptDenied("RECEIPT_CONFIG")
        if any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)
            or not isinstance(key, Ed25519PublicKey)
            for name, key in keys.items()
        ):
            raise ReceiptDenied("RECEIPT_CONFIG")
        self._keys = MappingProxyType(dict(keys))
        self._origin = origin

    def inspect_baseline(
        self,
        signed: SignedReceipt,
        plan: TestCommandPlan,
        junit: bytes,
        *,
        execution_id: UUID,
        source_tree_ref: str,
        expected: FailureExpectation,
        now: datetime,
    ) -> SignedBaselineInspection:
        """Bind actual bounded report bytes to the signature before parsing."""
        if len(junit) > 131072:
            raise ReceiptDenied("RECEIPT_REPORT_LIMIT")
        junit_ref = "sha256:" + hashlib.sha256(junit).hexdigest()
        verified = self.verify(
            signed,
            plan,
            execution_id=execution_id,
            source_tree_ref=source_tree_ref,
            junit_ref=junit_ref,
            now=now,
        )
        inspection = inspect_baseline(
            plan, junit, junit_ref, exit_code=1, expected=expected
        )
        return SignedBaselineInspection(
            status="MATCHED_SIGNED_REPORT"
            if inspection.status == "MATCHED_ASSERTION"
            else "REJECTED_SIGNED_REPORT",
            receipt=verified,
            inspection=inspection,
        )

    def verify(
        self,
        signed: SignedReceipt,
        plan: TestCommandPlan,
        *,
        execution_id: UUID,
        source_tree_ref: str,
        junit_ref: str,
        now: datetime,
    ) -> VerifiedReceipt:
        try:
            signed = SignedReceipt.model_validate(signed.model_dump())
            plan = TestCommandPlan.model_validate(plan.model_dump())
            key = self._keys.get(signed.key_id)
            if key is None:
                raise ReceiptDenied("RECEIPT_KEY_UNKNOWN")
            key.verify(
                bytes.fromhex(signed.signature), canonical(signed, unsigned=True)
            )
        except (InvalidSignature, ValidationError, ValueError):
            raise ReceiptDenied("RECEIPT_SIGNATURE_INVALID") from None
        receipt = signed.payload
        if now.tzinfo is None or now.utcoffset() is None:
            raise ReceiptDenied("RECEIPT_CLOCK_INVALID")
        age = (now - receipt.observed_at).total_seconds()
        if not -5 <= age <= 300:
            raise ReceiptDenied("RECEIPT_EXPIRED")
        if (
            receipt.run_id != plan.request.run_id
            or receipt.plan_ref != plan_ref(plan)
            or receipt.execution_id != execution_id
            or receipt.origin != self._origin
            or receipt.source_tree_ref != source_tree_ref
            or receipt.final_tree_ref != source_tree_ref
            or receipt.junit_ref != junit_ref
            or not receipt.terminal
            or receipt.timed_out
            or receipt.signal is not None
            or receipt.exit_code != 1
            or plan.request.phase != "BASELINE"
        ):
            raise ReceiptDenied("RECEIPT_BINDING_INVALID")
        return VerifiedReceipt(
            receipt_ref="sha256:" + hashlib.sha256(canonical(signed)).hexdigest(),
            execution_id=receipt.execution_id,
            junit_ref=receipt.junit_ref,
            origin=receipt.origin,
        )
