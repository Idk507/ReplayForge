"""Tiny synthetic reports and ephemeral keys; no remote execution proof."""

import hashlib

import pytest

from services.runner.receipts import (
    ReceiptDenied,
    SignedReplayReceipt,
    canonical,
    plan_ref,
)
from tests.unit.test_runner_receipts import fixture

XML = (
    b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
    b'<testcase classname="tests.test_regression" name="test_duplicate"/>'
    b"</testsuite>"
)


def replay_inputs(phase="CANDIDATE", body=XML, **updates):
    plan, receipt, now, _, verifier = fixture()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from packages.contracts.execution import pytest_argv
    from services.runner.receipts import ReceiptVerifier, plan_ref

    request = plan.request.model_copy(
        update={"phase": phase, "patch_ref": "sha256:" + "a" * 64}
    )
    plan = plan.model_copy(update={"request": request, "argv": pytest_argv(request)})
    receipt = receipt.model_copy(
        update={
            "plan_ref": plan_ref(plan),
            "exit_code": 0,
            "junit_ref": "sha256:" + hashlib.sha256(body).hexdigest(),
            **updates,
        }
    )
    key = Ed25519PrivateKey.generate()
    signed = SignedReplayReceipt(key_id="test", payload=receipt, signature="0" * 128)
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    verifier = ReceiptVerifier({"test": key.public_key()}, origin="TEST_TRANSPORT")
    args = dict(
        execution_id=receipt.execution_id,
        source_tree_ref=receipt.source_tree_ref,
        test_name="test_duplicate",
        now=now,
    )
    return verifier, signed, plan, args


def suite_inputs(body=XML, **updates):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from packages.contracts.execution import pytest_argv
    from services.runner.receipts import ReceiptVerifier

    verifier, signed, plan, args = replay_inputs(body=body, **updates)
    request = plan.request.model_copy(
        update={
            "phase": "EXISTING_SUITE",
            "test_paths": ("tests/test_regression.py", "tests/test_payments.py"),
        }
    )
    plan = plan.model_copy(update={"request": request, "argv": pytest_argv(request)})
    receipt = signed.payload.model_copy(
        update={
            "plan_ref": plan_ref(plan),
            "junit_ref": "sha256:" + hashlib.sha256(body).hexdigest(),
            **updates,
        }
    )
    signed = SignedReplayReceipt(key_id="test", payload=receipt, signature="0" * 128)
    # The test helper's verifier key is deliberately inaccessible here; create a
    # new ephemeral signer so suite coverage remains a clear test-only contract.
    key = Ed25519PrivateKey.generate()
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    verifier = ReceiptVerifier({"test": key.public_key()}, origin="TEST_TRANSPORT")
    args = {
        "execution_id": receipt.execution_id,
        "source_tree_ref": receipt.source_tree_ref,
        "now": args["now"],
        "test_paths": request.test_paths,
    }
    return verifier, signed, plan, args


SUITE_XML = (
    b'<testsuite tests="2" failures="0" errors="0" skipped="0">'
    b'<testcase classname="tests.test_regression" name="test_duplicate"/>'
    b'<testcase classname="tests.test_payments" name="test_payment"/>'
    b"</testsuite>"
)


def test_full_existing_suite_is_authenticated_and_remains_test_origin():
    verifier, signed, plan, args = suite_inputs(SUITE_XML)
    result = verifier.inspect_existing_suite(signed, plan, SUITE_XML, **args)
    assert result.status == "MATCHED_SIGNED_REPORT"
    assert result.tests == 2
    assert result.receipt.origin == "TEST_TRANSPORT"
    assert result.can_authorize_verification is False


def test_existing_suite_requires_every_declared_test_file():
    partial = SUITE_XML.replace(
        b'<testcase classname="tests.test_payments" name="test_payment"/>', b""
    ).replace(b'tests="2"', b'tests="1"')
    verifier, signed, plan, args = suite_inputs(partial)
    result = verifier.inspect_existing_suite(signed, plan, partial, **args)
    assert result.status == "REJECTED_SIGNED_REPORT"
    assert result.reason == "INCOMPLETE_SUITE"


def test_existing_suite_rejects_any_failed_or_skipped_test():
    failed = SUITE_XML.replace(b'failures="0"', b'failures="1"').replace(
        b'name="test_payment"/>', b'name="test_payment"><failure/></testcase>'
    )
    verifier, signed, plan, args = suite_inputs(failed)
    result = verifier.inspect_existing_suite(signed, plan, failed, **args)
    assert result.status == "REJECTED_SIGNED_REPORT"
    assert result.reason == "INVALID_REPORT"


def test_existing_suite_rejects_altered_report_and_wrong_declared_paths():
    verifier, signed, plan, args = suite_inputs(SUITE_XML)
    with pytest.raises(ReceiptDenied):
        verifier.inspect_existing_suite(signed, plan, SUITE_XML + b" ", **args)
    with pytest.raises(ReceiptDenied):
        verifier.inspect_existing_suite(
            signed,
            plan,
            SUITE_XML,
            **{**args, "test_paths": ("tests/test_regression.py",)},
        )


@pytest.mark.parametrize("phase", ["CANDIDATE", "CLEAN_REPLAY"])
def test_exact_passing_regression_is_authenticated_without_verdict_authority(phase):
    verifier, signed, plan, args = replay_inputs(phase)
    result = verifier.inspect_replay(signed, plan, XML, **args)
    assert result.status == "MATCHED_SIGNED_REPORT"
    assert result.tests == 1
    assert result.receipt.origin == "TEST_TRANSPORT"
    assert result.can_authorize_verification is False


@pytest.mark.parametrize(
    "body",
    [
        XML.replace(b"test_duplicate", b"test_other"),
        XML.replace(b'tests="1"', b'tests="0"'),
        XML.replace(b"/></testsuite>", b"><skipped/></testcase></testsuite>"),
        XML.replace(b"/></testsuite>", b"><failure/></testcase></testsuite>"),
        b'<!DOCTYPE testsuite [<!ENTITY x "boom">]>' + XML,
        b"not XML",
        b" ",
        pytest.param(b"x" * 131073, id="oversize"),
    ],
)
def test_invalid_or_missing_regression_cannot_pass(body):
    verifier, signed, plan, args = replay_inputs(body=body)
    if len(body) > 131072:
        with pytest.raises(ReceiptDenied):
            verifier.inspect_replay(signed, plan, body, **args)
    else:
        assert (
            verifier.inspect_replay(signed, plan, body, **args).status
            == "REJECTED_SIGNED_REPORT"
        )


@pytest.mark.parametrize(
    "update",
    [
        {"exit_code": 1},
        {"exit_code": 2},
        {"timed_out": True},
        {"terminal": False},
        {"signal": 9},
        {"final_tree_ref": "sha256:" + "0" * 64},
    ],
)
def test_bad_process_observations_never_pass(update):
    verifier, signed, plan, args = replay_inputs(**update)
    if "exit_code" in update:
        assert (
            verifier.inspect_replay(signed, plan, XML, **args).status
            == "REJECTED_SIGNED_REPORT"
        )
    else:
        with pytest.raises(ReceiptDenied):
            verifier.inspect_replay(signed, plan, XML, **args)


def test_tampered_bytes_and_wrong_execution_are_denied():
    verifier, signed, plan, args = replay_inputs()
    with pytest.raises(ReceiptDenied):
        verifier.inspect_replay(signed, plan, XML + b" ", **args)
    from uuid import uuid4

    with pytest.raises(ReceiptDenied):
        verifier.inspect_replay(signed, plan, XML, **{**args, "execution_id": uuid4()})


def test_baseline_envelope_cannot_be_reused_for_replay():
    from services.runner.receipts import SignedReceipt

    verifier, signed, plan, args = replay_inputs()
    baseline = SignedReceipt(
        key_id=signed.key_id, payload=signed.payload, signature=signed.signature
    )
    with pytest.raises(ReceiptDenied):
        verifier.inspect_replay(baseline, plan, XML, **args)


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_consistently_counted_nonpassing_report_is_rejected(outcome):
    field = {"failure": "failures", "error": "errors", "skipped": "skipped"}[outcome]
    body = XML.replace(f'{field}="0"'.encode(), f'{field}="1"'.encode()).replace(
        b"/></testsuite>", f"><{outcome}/></testcase></testsuite>".encode()
    )
    verifier, signed, plan, args = replay_inputs(body=body)
    assert (
        verifier.inspect_replay(signed, plan, body, **args).reason == "INVALID_REPORT"
    )


@pytest.mark.parametrize(
    "change", ["patch", "test", "phase", "clock", "origin", "signature"]
)
def test_replay_identity_freshness_and_signature_are_bound(change):
    from datetime import timedelta

    verifier, signed, plan, args = replay_inputs()
    if change in {"patch", "test", "phase"}:
        updates = {
            "patch": {"patch_ref": "sha256:" + "b" * 64},
            "test": {"reproduction_test_ref": "sha256:" + "9" * 64},
            "phase": {"phase": "CLEAN_REPLAY"},
        }[change]
        plan = plan.model_copy(
            update={"request": plan.request.model_copy(update=updates)}
        )
    elif change == "clock":
        args["now"] += timedelta(seconds=301)
    elif change == "origin":
        verifier, signed, plan, args = replay_inputs(origin="REMOTE_SERVICE")
    else:
        signed = signed.model_copy(update={"signature": "0" * 128})
    with pytest.raises(ReceiptDenied):
        verifier.inspect_replay(signed, plan, XML, **args)
