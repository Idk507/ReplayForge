"""Ephemeral test signer; no real runner or production key provisioning."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.runner.commands import plan_pytest
from services.runner.receipts import (
    ExecutionReceipt,
    ReceiptDenied,
    ReceiptVerifier,
    SignedReceipt,
    canonical,
    plan_ref,
)
from tests.unit.test_command_policy import inputs


def fixture():
    run, grant, owner, request = inputs()
    plan = plan_pytest(request, run, grant, owner)
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    receipt = ExecutionReceipt(
        execution_id=uuid4(),
        run_id=run.run_id,
        plan_ref=plan_ref(plan),
        source_tree_ref="sha256:" + "e" * 64,
        final_tree_ref="sha256:" + "e" * 64,
        junit_ref="sha256:" + "f" * 64,
        origin="TEST_TRANSPORT",
        observed_at=now,
        exit_code=1,
        terminal=True,
        timed_out=False,
        signal=None,
    )

    def sign(payload=receipt, key_id="test"):
        unsigned = SignedReceipt(payload=payload, key_id=key_id, signature="0" * 128)
        return unsigned.model_copy(
            update={"signature": key.sign(canonical(unsigned, unsigned=True)).hex()}
        )

    verifier = ReceiptVerifier({"test": key.public_key()}, origin="TEST_TRANSPORT")
    return plan, receipt, now, sign, verifier


def check(verifier, signed, plan, receipt, reference_now, **kwargs):
    return verifier.verify(
        signed,
        plan,
        execution_id=kwargs.get("execution_id", receipt.execution_id),
        source_tree_ref=kwargs.get("source_tree_ref", receipt.source_tree_ref),
        junit_ref=kwargs.get("junit_ref", receipt.junit_ref),
        now=kwargs.get("now", reference_now),
    )


def test_signature_and_exact_correlation_accepted_as_test_evidence() -> None:
    plan, receipt, now, sign, verifier = fixture()
    result = check(verifier, sign(), plan, receipt, now)
    assert result.origin == "TEST_TRANSPORT"
    assert result.can_authorize_patching is False
    assert result.execution_id == receipt.execution_id


@pytest.mark.parametrize("tamper", [False, True])
def test_signed_receipt_binds_actual_junit_bytes(tamper: bool) -> None:
    import hashlib

    from services.runner.baseline import FailureExpectation
    from tests.unit.test_baseline_inspection import XML

    plan, receipt, now, sign, verifier = fixture()
    receipt = receipt.model_copy(
        update={"junit_ref": "sha256:" + hashlib.sha256(XML).hexdigest()}
    )
    signed = sign(receipt)
    body = XML.replace(b"RF_DUPLICATE_TRANSACTION", b"OTHER") if tamper else XML
    args = dict(
        execution_id=receipt.execution_id,
        source_tree_ref=receipt.source_tree_ref,
        expected=FailureExpectation(
            test_name="test_duplicate", assertion_marker="RF_DUPLICATE_TRANSACTION"
        ),
        now=now,
    )
    if tamper:
        with pytest.raises(ReceiptDenied):
            verifier.inspect_baseline(signed, plan, body, **args)
    else:
        result = verifier.inspect_baseline(signed, plan, body, **args)
        assert result.status == "MATCHED_SIGNED_REPORT"
        assert result.receipt.origin == "TEST_TRANSPORT"
        assert result.can_authorize_patching is False


@pytest.mark.parametrize("case", ["empty", "limit", "name", "type"])
def test_bad_key_registry_fails_closed(case: str) -> None:
    key = Ed25519PrivateKey.generate().public_key()
    keys = {"test": key}
    if case == "empty":
        keys = {}
    elif case == "limit":
        keys = {str(i): key for i in range(17)}
    elif case == "name":
        keys = {"bad id": key}
    else:
        keys = {"test": object()}
    with pytest.raises(ReceiptDenied, match="RECEIPT_CONFIG"):
        ReceiptVerifier(keys, origin="TEST_TRANSPORT")


@pytest.mark.parametrize(
    "case",
    [
        "tamper",
        "signature",
        "key_id",
        "execution",
        "tree",
        "report",
        "expired",
        "future",
        "plan",
        "origin",
        "timeout",
        "signal",
        "terminal",
        "final_tree",
        "run",
    ],
)
def test_forgery_rebinding_and_invalid_process_state_rejected(case) -> None:
    plan, receipt, now, sign, verifier = fixture()
    signed = sign()
    kwargs = {}
    if case == "tamper":
        signed = signed.model_copy(
            update={"payload": receipt.model_copy(update={"exit_code": 0})}
        )
    elif case == "signature":
        signed = signed.model_copy(update={"signature": "0" * 128})
    elif case == "key_id":
        signed = sign(key_id="unknown")
    elif case == "execution":
        kwargs["execution_id"] = uuid4()
    elif case == "tree":
        kwargs["source_tree_ref"] = "sha256:" + "a" * 64
    elif case == "report":
        kwargs["junit_ref"] = "sha256:" + "a" * 64
    elif case == "expired":
        kwargs["now"] = now + timedelta(seconds=301)
    elif case == "future":
        kwargs["now"] = now - timedelta(seconds=6)
    else:
        changes = {
            "plan": {"plan_ref": "sha256:" + "a" * 64},
            "origin": {"origin": "REMOTE_SERVICE"},
            "timeout": {"timed_out": True},
            "signal": {"signal": 9},
            "terminal": {"terminal": False},
            "final_tree": {"final_tree_ref": "sha256:" + "a" * 64},
            "run": {"run_id": uuid4()},
        }
        signed = sign(receipt.model_copy(update=changes[case]))
    with pytest.raises(ReceiptDenied):
        check(verifier, signed, plan, receipt, now, **kwargs)
