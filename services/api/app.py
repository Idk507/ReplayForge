"""Loopback-only authenticated run API backed by real transactional storage."""

import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID, uuid4, uuid5

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from packages.contracts.context import ContextBundle
from packages.contracts.run import ArtifactID, CommitSHA, RunState
from packages.contracts.source import SourceManifest
from packages.policies.repository import admit
from packages.policies.transitions import TransitionError
from services.context.incident import prepare_incident
from services.persistence.evidence import EvidenceStore
from services.persistence.store import Store, StoreError
from services.verifier.manifest import EvidenceManifest


class LocalAuth(BaseModel):
    """One local operator; identity/repository scope is server configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    token: SecretStr | None = None
    owner_id: UUID | None = None
    approved_repositories: tuple[UUID, ...] = Field(default=(), max_length=100)

    @field_validator("token")
    @classmethod
    def safe_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not 32 <= len(raw) <= 256 or not all(33 <= ord(c) <= 126 for c in raw):
                raise ValueError("AUTH_TOKEN_INVALID")
        return value


class CreateRun(BaseModel):
    """Raw incident stays ephemeral; only bounded redacted input is persisted."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    repository_id: UUID
    commit_sha: CommitSHA
    incident_text: str = Field(min_length=1, max_length=20000, repr=False)
    idempotency_key: UUID


class CancelRun(BaseModel):
    """Expected checkpoint version prevents stale cancellation requests."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_version: int = Field(strict=True, ge=0)


class LocalBoundary:
    """Reject remote peers and cap request bytes before FastAPI validation."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        if not client or client[0] not in {"127.0.0.1", "::1"}:
            await JSONResponse({"error_class": "LOCAL_ONLY"}, status_code=403)(
                scope, receive, send
            )
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 32768:
                await JSONResponse({"error_class": "BODY_TOO_LARGE"}, status_code=413)(
                    scope, receive, send
                )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def create_app(store: Store, auth: LocalAuth) -> FastAPI:
    """Construct a local-only app; no token or repository grants by default."""
    auth = LocalAuth.model_validate(auth.model_dump())
    app = FastAPI(
        title="ReplayForge local development",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(LocalBoundary)

    def identity(request: Request) -> UUID:
        if auth.token is None or auth.owner_id is None:
            raise HTTPException(503, "AUTH_NOT_CONFIGURED")
        value = request.headers.get("authorization")
        if not value:
            raise HTTPException(401, "AUTH_REQUIRED")
        if not hmac.compare_digest(
            value.encode(), ("Bearer " + auth.token.get_secret_value()).encode()
        ):
            raise HTTPException(403, "AUTH_DENIED")
        return auth.owner_id

    @app.exception_handler(RequestValidationError)
    async def invalid_input(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            {"error_class": "VALIDATION_ERROR", "trace_id": str(uuid4())},
            status_code=422,
        )

    @app.exception_handler(StoreError)
    async def database_error(request: Request, error: StoreError) -> JSONResponse:
        code = str(error)
        status = 404 if code == "NOT_FOUND" else 409
        if code.startswith("DB_"):
            status = 503
        return JSONResponse(
            {"error_class": code, "trace_id": str(uuid4())}, status_code=status
        )

    @app.exception_handler(TransitionError)
    async def transition_error(
        request: Request, error: TransitionError
    ) -> JSONResponse:
        return JSONResponse(
            {"error_class": str(error), "trace_id": str(uuid4())}, status_code=409
        )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live", "mode": "LOCAL_DEVELOPMENT"}

    @app.post("/api/v1/runs", status_code=202)
    def create(
        payload: CreateRun, owner: Annotated[UUID, Depends(identity)]
    ) -> RunState:
        if payload.repository_id not in auth.approved_repositories:
            raise HTTPException(403, "REPOSITORY_DENIED")
        try:
            grant = store.repository(payload.repository_id, owner)
        except StoreError as error:
            if str(error) == "NOT_FOUND":
                raise HTTPException(403, "REPOSITORY_DENIED") from None
            raise
        if not admit(grant, owner, payload.repository_id, payload.commit_sha).allowed:
            raise HTTPException(403, "REPOSITORY_DENIED")
        canonical = json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        state = RunState(
            run_id=uuid4(),
            incident_id=uuid5(owner, str(payload.idempotency_key)),
            repository_id=payload.repository_id,
            source_commit=payload.commit_sha,
            policy_version="local-v1",
            trace_id=uuid4(),
        )
        try:
            report = prepare_incident(
                payload.incident_text,
                known_secrets=(auth.token,) if auth.token is not None else (),
            )
        except ValueError:
            raise HTTPException(422, "INCIDENT_INPUT_INVALID") from None
        return store.create(
            state, owner, payload.idempotency_key, digest, incident=report
        )

    @app.get("/api/v1/runs/{run_id}")
    def get(run_id: UUID, owner: Annotated[UUID, Depends(identity)]) -> RunState:
        return store.get(run_id, owner)

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id}")
    def artifact(
        run_id: UUID, artifact_id: ArtifactID, owner: Annotated[UUID, Depends(identity)]
    ) -> ContextBundle:
        return store.context(run_id, owner, artifact_id)

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel(
        run_id: UUID, payload: CancelRun, owner: Annotated[UUID, Depends(identity)]
    ) -> RunState:
        return store.cancel(run_id, owner, expected_version=payload.expected_version)

    @app.get("/api/v1/runs/{run_id}/evidence/{manifest_ref}")
    def evidence_manifest(
        run_id: UUID,
        manifest_ref: ArtifactID,
        response: Response,
        owner: Annotated[UUID, Depends(identity)],
    ) -> EvidenceManifest:
        manifest, _ = EvidenceStore(store).read(run_id, owner, manifest_ref)
        response.headers["Cache-Control"] = "no-store"
        return manifest

    @app.get("/api/v1/runs/{run_id}/evidence/{manifest_ref}/artifacts/{artifact_id}")
    def evidence_artifact(
        run_id: UUID,
        manifest_ref: ArtifactID,
        artifact_id: ArtifactID,
        owner: Annotated[UUID, Depends(identity)],
    ) -> Response:
        manifest, artifacts = EvidenceStore(store).read(run_id, owner, manifest_ref)
        names = {
            entry.name for entry in manifest.entries if entry.artifact_id == artifact_id
        }
        for artifact in artifacts:
            if artifact.name in names:
                # Names are constrained to a short ASCII token; never paths or
                # user-controlled MIME types. Binary/HTML always downloads inertly.
                return Response(
                    content=artifact.content,
                    media_type="application/octet-stream",
                    headers={
                        "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                        "Content-Disposition": (
                            f'attachment; filename="{artifact.name}"'
                        ),
                    },
                )
        raise HTTPException(404, "NOT_FOUND")

    @app.get("/api/v1/runs/{run_id}/source-manifests/{artifact_id}")
    def source_manifest(
        run_id: UUID, artifact_id: ArtifactID, owner: Annotated[UUID, Depends(identity)]
    ) -> SourceManifest:
        return store.source_manifest(run_id, owner, artifact_id)

    @app.get("/api/v1/runs/{run_id}/events")
    def events(
        run_id: UUID,
        request: Request,
        owner: Annotated[UUID, Depends(identity)],
        after: Annotated[int, Field(ge=0)] = 0,
    ) -> StreamingResponse:
        cursor = request.headers.get("last-event-id")
        if cursor is not None:
            if not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 18:
                raise HTTPException(400, "INVALID_CURSOR")
            after = int(cursor)
        page = store.events(run_id, owner, after)

        # A finite replay page releases all DB resources before streaming.
        # Reconnection supplies the last delivered sequence; no fabricated ticks.
        def frames() -> Iterator[str]:
            for event in page:
                yield (
                    f"id: {event.sequence}\nevent: {event.event_type}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
            yield ": replay-page-complete\n\n"

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    return app
