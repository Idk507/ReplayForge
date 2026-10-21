"""Pure human-review binding policy, never a serialized publication capability."""

import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from packages.contracts.run import ArtifactID, CommitSHA


class ApprovalDenied(Exception):
    """Safe denial without patch, credentials or reviewer details."""


class ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class ApprovalBinding(ClosedModel):
    """Exact current verified state, constructed by trusted application code.

    patch_ref is the SHA256 of the admitted diff bytes. verification_ref binds
    the retained authenticated verdict. Neither reference alone proves approval.
    """

    run_id: UUID
    repository_id: UUID
    owner_id: UUID
    source_commit: CommitSHA
    patch_ref: ArtifactID
    verification_ref: ArtifactID
    target_branch: str = Field(min_length=1, max_length=120)
    policy_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")

    @model_validator(mode="after")
    def branch(self) -> Self:
        # Deliberately narrow Git branch vocabulary for the initial publisher.
        if (
            not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", self.target_branch)
            or any(
                part in {"", ".", ".."}
                or part.startswith(".")
                or part.endswith((".", ".lock"))
                for part in self.target_branch.split("/")
            )
            or ".." in self.target_branch
        ):
            raise ValueError("APPROVAL_TARGET_INVALID")
        return self


class ReviewPermission(ClosedModel):
    """Authenticated reviewer and current grant loaded outside model control.

    Never accept this object from a browser/model as proof of permissions.
    Repository membership/revocation must be reloaded on each privileged action.
    """

    reviewer_id: UUID
    repository_id: UUID
    owner_id: UUID


class HumanDecision(ClosedModel):
    """A private review record; caller must authenticate its durable provenance."""

    binding: ApprovalBinding
    reviewer_id: UUID
    decision: Literal["approve", "reject"]
    nonce: UUID
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    can_authorize_publication: Literal[False] = False


def validate_decision(
    decision: HumanDecision,
    expected: ApprovalBinding,
    permission: ReviewPermission,
    *,
    now: datetime,
    consumed: bool,
) -> None:
    """Fail closed on stale, changed, rejected, unauthorized or consumed review.

    This calculation does not authenticate a human, load a VERIFIED checkpoint,
    persist/consume a nonce or publish. Those operations must be atomic in the
    future approval journal/publisher. A deserialized HumanDecision is not proof
    of consent; do not expose this function as a public authorization endpoint.
    """
    decision = HumanDecision.model_validate(decision.model_dump())
    expected = ApprovalBinding.model_validate(expected.model_dump())
    permission = ReviewPermission.model_validate(permission.model_dump())
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ApprovalDenied("APPROVAL_CLOCK_INVALID")
    if type(consumed) is not bool or consumed:
        raise ApprovalDenied("APPROVAL_NONCE_UNAVAILABLE")
    if decision.decision != "approve" or decision.binding != expected:
        raise ApprovalDenied("APPROVAL_BINDING_DENIED")
    if (
        decision.reviewer_id != permission.reviewer_id
        or expected.repository_id != permission.repository_id
        or expected.owner_id != permission.owner_id
    ):
        raise ApprovalDenied("APPROVAL_REVIEWER_DENIED")
    if (
        not decision.issued_at <= now < decision.expires_at
        or not 0 < (decision.expires_at - decision.issued_at).total_seconds() <= 900
    ):
        raise ApprovalDenied("APPROVAL_EXPIRED_OR_FUTURE")
