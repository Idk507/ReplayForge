"""Session-authenticated exact human review; no automatic publication."""

from typing import Literal, Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.approval.journal import ApprovalJournal
from services.approval.policy import (
    ApprovalBinding,
    ApprovalDenied,
    HumanDecision,
    ReviewPermission,
)
from services.persistence.store import Store


class ReviewAccess(Protocol):
    def identify(self, request: Request) -> UUID: ...
    def authorize(
        self, owner: UUID, repository_id: UUID, *, write: bool = False
    ) -> None: ...


class ReviewView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    binding: ApprovalBinding
    expected_version: int = Field(strict=True, ge=0)


class ReviewResponse(ReviewView):
    receipt: HumanDecision | None
    publication_version: int | None


class ReviewRequest(ReviewView):
    decision: Literal["approve", "reject"]
    nonce: UUID


def install_review_routes(
    app: FastAPI, store: Store, access: ReviewAccess, *, target_branch: str
) -> None:
    """Install only on the session app, with an operator-selected branch policy.

    Identity+CSRF and current GitHub push/installation membership come from the
    session adapter. Browser JSON contains a decision and exact reviewed binding,
    never ReviewPermission. The journal rechecks current evidence and version.
    """
    # Validate branch policy without touching storage or provider services.
    ApprovalBinding(
        run_id=UUID(int=1),
        repository_id=UUID(int=1),
        owner_id=UUID(int=1),
        source_commit="a" * 40,
        patch_ref="sha256:" + "a" * 64,
        verification_ref="sha256:" + "a" * 64,
        target_branch=target_branch,
        policy_version="repository-v1",
    )
    journal = ApprovalJournal(store, target_branch=target_branch)

    def current(request: Request, run_id: UUID) -> tuple[ReviewView, ReviewPermission]:
        owner = access.identify(request)
        run = store.get(run_id, owner)
        access.authorize(owner, run.repository_id, write=True)
        if not run.patch_attempt_refs or run.verification_ref is None:
            raise ApprovalDenied("APPROVAL_VERIFIED_EVIDENCE_REQUIRED")
        return ReviewView(
            binding=ApprovalBinding(
                run_id=run.run_id,
                repository_id=run.repository_id,
                owner_id=owner,
                source_commit=run.source_commit,
                patch_ref=run.patch_attempt_refs[-1],
                verification_ref=run.verification_ref,
                target_branch=target_branch,
                policy_version=run.policy_version,
            ),
            expected_version=run.version,
        ), ReviewPermission(
            reviewer_id=owner, repository_id=run.repository_id, owner_id=owner
        )

    @app.exception_handler(ApprovalDenied)
    async def denied(request: Request, error: ApprovalDenied) -> JSONResponse:
        return JSONResponse(
            {"error_class": str(error), "trace_id": str(uuid4())}, status_code=409
        )

    @app.get("/api/v1/runs/{run_id}/review")
    def review(run_id: UUID, request: Request) -> ReviewResponse:
        view, permission = current(request, run_id)
        saved = journal.read(view.binding, permission)
        return ReviewResponse(
            **view.model_dump(),
            receipt=None if saved is None else saved[0],
            publication_version=None if saved is None else saved[1],
        )

    @app.post("/api/v1/runs/{run_id}/approval")
    def decide(run_id: UUID, payload: ReviewRequest, request: Request) -> HumanDecision:
        view, permission = current(request, run_id)
        if payload.binding != view.binding:
            raise ApprovalDenied("APPROVAL_BINDING_DENIED")
        return journal.record(
            view.binding,
            permission,
            decision=payload.decision,
            nonce=payload.nonce,
            expected_version=payload.expected_version,
        )
