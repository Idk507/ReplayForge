"""Session-to-approval-to-reduced-installation publication composition."""

from collections.abc import Callable
from uuid import UUID

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.approval.journal import ApprovalJournal, PublicationIntent
from services.approval.policy import ApprovalBinding, ApprovalDenied, ReviewPermission
from services.auth.access import AuthAccess
from services.auth.github import AuthDenied
from services.auth.installation import InstallationTokenBroker
from services.orchestrator.publication import publish_approved
from services.persistence.store import Store
from services.publication.github import GitHubPublisher, PullRequestResult
from services.publication.patch import PublicationDenied


class PublishRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    binding: ApprovalBinding
    nonce: UUID
    expected_version: int = Field(strict=True, ge=0)


def install_publication_route(
    app: FastAPI,
    store: Store,
    access: AuthAccess,
    broker_factory: Callable[[], InstallationTokenBroker],
    *,
    target_branch: str,
    allow_live_writes: bool = False,
    publication_transport: httpx.BaseTransport | None = None,
) -> None:
    """Explicit privileged deployment wiring, with no startup/provider I/O.

    The key factory lives only in the auth/publication service. Neither browser
    nor worker receives an App key or installation token. Every publisher POST
    reloads session, membership, current evidence and the same durable approval.
    No route is installed by default and no endpoint generates human approval.
    """
    approval = ApprovalJournal(store, target_branch=target_branch)

    @app.exception_handler(PublicationDenied)
    async def denied(request: Request, error: PublicationDenied) -> JSONResponse:
        return JSONResponse({"error_class": str(error)}, status_code=409)

    @app.post("/api/v1/runs/{run_id}/publication")
    def publish(
        run_id: UUID, payload: PublishRequest, request: Request
    ) -> PullRequestResult:
        owner = access.identify(request)
        binding = payload.binding
        if (
            binding.run_id != run_id
            or binding.owner_id != owner
            or binding.target_branch != target_branch
        ):
            raise ApprovalDenied("APPROVAL_BINDING_DENIED")
        if publication_transport is None and allow_live_writes is not True:
            raise AuthDenied("AUTH_INSTALLATION_LIVE_DENIED")

        def permission() -> ReviewPermission:
            if access.identify(request) != owner:
                raise AuthDenied("AUTH_REQUIRED")
            run = store.get(run_id, owner)
            if run.repository_id != binding.repository_id:
                raise AuthDenied("AUTH_REPOSITORY_DENIED")
            access.authorize(owner, binding.repository_id, write=True)
            return ReviewPermission(
                reviewer_id=owner, repository_id=binding.repository_id, owner_id=owner
            )

        def authorize_mint() -> PublicationIntent:
            return approval.claim(
                binding,
                permission(),
                nonce=payload.nonce,
                expected_version=payload.expected_version,
            )

        def publisher() -> GitHubPublisher:
            scope = access.write_scope(owner, binding.repository_id)
            with broker_factory() as broker:
                expected_origin = (
                    "TEST_TRANSPORT"
                    if publication_transport is not None
                    else "REMOTE_SERVICE"
                )
                if broker.origin != expected_origin or (
                    broker.origin == "REMOTE_SERVICE" and not allow_live_writes
                ):
                    raise AuthDenied("AUTH_INSTALLATION_LIVE_DENIED")
                credential = broker.mint(scope, authorize_mint)
            if credential.origin != expected_origin:
                raise AuthDenied("AUTH_INSTALLATION_SCOPE_DENIED")
            return GitHubPublisher(
                credential.token,
                allow_live_writes=allow_live_writes,
                transport=publication_transport,
            )

        return publish_approved(
            store,
            binding,
            payload.nonce,
            permission,
            publisher,
            expected_version=payload.expected_version,
            allow_live_writes=allow_live_writes,
        )
