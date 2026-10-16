"""Provider lifecycle metadata cannot assert verified execution or embed logs."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.persistence.operations import OperationRecord


def record_data() -> dict[str, object]:
    return {
        "execution_id": uuid4(),
        "run_id": uuid4(),
        "action_ref": "sha256:" + "a" * 64,
        "provider": "TEST_TRANSPORT",
        "phase": "SUBMISSION_UNKNOWN",
        "operation_id": None,
        "recorded_at": datetime.now(UTC),
        "trace_id": uuid4(),
        "worker_id": uuid4(),
        "run_version": 1,
    }


@pytest.mark.parametrize(
    "update",
    [
        {"phase": "COMPUTE_TERMINAL"},
        {"phase": "OPERATION_KNOWN"},
        {"operation_id": str(uuid4())},
        {"phase": "VERIFIED"},
        {"provider": "https://attacker.invalid"},
        {"stdout": "secret"},
        {"can_authorize_execution": True},
        {"recorded_at": "2026-10-16T00:00:00"},
        {"run_version": True},
    ],
)
def test_operation_record_rejects_unsafe_claims(update: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OperationRecord.model_validate({**record_data(), **update})


def test_operation_record_is_inert_and_immutable() -> None:
    record = OperationRecord.model_validate(record_data())
    assert record.evidence_origin == "UNVERIFIED_ADAPTER_INPUT"
    assert record.can_authorize_execution is False
    with pytest.raises(ValidationError):
        record.phase = "COMPUTE_TERMINAL"
