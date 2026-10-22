"""Approval binding kernel only; no human identity or PR integration is faked."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from services.approval.policy import (
    ApprovalBinding,
    ApprovalDenied,
    HumanDecision,
    ReviewPermission,
    validate_decision,
)


def inputs():
    now = datetime.now(UTC)
    binding = ApprovalBinding(
        run_id=uuid4(),
        repository_id=uuid4(),
        owner_id=uuid4(),
        source_commit="a" * 40,
        patch_ref="sha256:" + "b" * 64,
        verification_ref="sha256:" + "c" * 64,
        target_branch="main",
        policy_version="mandatory-gates-v1",
    )
    permission = ReviewPermission(
        reviewer_id=uuid4(),
        repository_id=binding.repository_id,
        owner_id=binding.owner_id,
    )
    decision = HumanDecision(
        binding=binding,
        reviewer_id=permission.reviewer_id,
        decision="approve",
        nonce=uuid4(),
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    return decision, binding, permission, now


def test_exact_approval_has_no_publication_authority():
    decision, binding, permission, now = inputs()
    validate_decision(decision, binding, permission, now=now, consumed=False)
    assert decision.can_authorize_publication is False


@pytest.mark.parametrize(
    "fault",
    [
        "patch",
        "source",
        "target",
        "verdict",
        "reviewer",
        "repository",
        "owner",
        "expiry",
        "future",
        "ttl",
        "used",
        "reject",
    ],
)
def test_changed_binding_or_invalid_human_decision_is_denied(fault):
    decision, binding, permission, now = inputs()
    if fault in {"patch", "source", "target", "verdict"}:
        update = {
            "patch": {"patch_ref": "sha256:" + "d" * 64},
            "source": {"source_commit": "d" * 40},
            "target": {"target_branch": "release"},
            "verdict": {"verification_ref": "sha256:" + "d" * 64},
        }[fault]
        binding = binding.model_copy(update=update)
    elif fault in {"reviewer", "repository", "owner"}:
        field = {
            "reviewer": "reviewer_id",
            "repository": "repository_id",
            "owner": "owner_id",
        }[fault]
        permission = permission.model_copy(update={field: uuid4()})
    elif fault == "expiry":
        now += timedelta(minutes=10)
    elif fault == "future":
        now -= timedelta(seconds=1)
    elif fault == "ttl":
        decision = decision.model_copy(
            update={"expires_at": now + timedelta(minutes=16)}
        )
    elif fault == "reject":
        decision = decision.model_copy(update={"decision": "reject"})
    with pytest.raises(ApprovalDenied):
        validate_decision(
            decision, binding, permission, now=now, consumed=fault == "used"
        )


@pytest.mark.parametrize(
    "branch", ["../main", "main..old", "main.lock", "main/", "-main", "main@{1}"]
)
def test_unsafe_target_branch_is_rejected(branch):
    from pydantic import ValidationError

    _, binding, _, _ = inputs()
    with pytest.raises(ValidationError):
        ApprovalBinding.model_validate(
            {**binding.model_dump(), "target_branch": branch}
        )


def test_naive_clock_and_non_boolean_consumption_are_denied():
    decision, binding, permission, now = inputs()
    with pytest.raises(ApprovalDenied):
        validate_decision(
            decision, binding, permission, now=now.replace(tzinfo=None), consumed=False
        )
    with pytest.raises(ApprovalDenied):
        validate_decision(decision, binding, permission, now=now, consumed=0)
