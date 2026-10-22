"""Synthetic pair correlation; never claims fresh provider compute."""

from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.runner.receipts import ReceiptDenied, ReceiptVerifier, canonical, plan_ref
from services.verifier.replay import ReplayActions, ReplayReport, inspect_replay_pair
from tests.unit.test_replay_receipts import XML, replay_inputs


def inputs():
    _, candidate, plan, args = replay_inputs()
    clean_plan = plan.model_copy(
        update={"request": plan.request.model_copy(update={"phase": "CLEAN_REPLAY"})}
    )
    clean = candidate.model_copy(
        update={
            "payload": candidate.payload.model_copy(
                update={"execution_id": uuid4(), "plan_ref": plan_ref(clean_plan)}
            )
        }
    )
    key = Ed25519PrivateKey.generate()

    def sign(report):
        return report.model_copy(
            update={"signature": key.sign(canonical(report, unsigned=True)).hex()}
        )

    actions = ReplayActions(
        candidate_plan=plan,
        clean_plan=clean_plan,
        baseline_execution_id=uuid4(),
        candidate_execution_id=candidate.payload.execution_id,
        clean_execution_id=clean.payload.execution_id,
        patched_tree_ref=candidate.payload.source_tree_ref,
        test_name="test_duplicate",
    )
    return (
        actions,
        ReplayReport(signed=sign(candidate), junit=XML),
        ReplayReport(signed=sign(clean), junit=XML),
        ReceiptVerifier({"test": key.public_key()}, origin="TEST_TRANSPORT"),
        args["now"],
    )


def test_both_authenticated_passes_are_returned_without_verification_authority():
    actions, candidate, clean, verifier, now = inputs()
    results = inspect_replay_pair(actions, candidate, clean, verifier, now=now)
    assert all(r.status == "MATCHED_SIGNED_REPORT" for r in results)
    assert all(r.can_authorize_verification is False for r in results)


@pytest.mark.parametrize(
    "change", ["operation", "baseline", "test", "image", "patch", "phase", "swapped"]
)
def test_reused_operation_or_different_replay_inputs_are_denied(change):
    actions, candidate, clean, verifier, now = inputs()
    if change == "operation":
        actions = actions.model_copy(
            update={"clean_execution_id": actions.candidate_execution_id}
        )
    elif change == "baseline":
        actions = actions.model_copy(
            update={"baseline_execution_id": actions.candidate_execution_id}
        )
    elif change == "swapped":
        candidate, clean = clean, candidate
    else:
        field = {
            "test": "reproduction_test_ref",
            "image": "image_digest",
            "patch": "patch_ref",
            "phase": "phase",
        }[change]
        request = actions.clean_plan.request.model_copy(
            update={field: "CANDIDATE" if change == "phase" else "sha256:" + "9" * 64}
        )
        actions = actions.model_copy(
            update={
                "clean_plan": actions.clean_plan.model_copy(update={"request": request})
            }
        )
    with pytest.raises(ReceiptDenied):
        inspect_replay_pair(actions, candidate, clean, verifier, now=now)
