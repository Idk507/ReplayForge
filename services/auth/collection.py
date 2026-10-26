"""Auth-service collection boundary; GitHub/vault credentials never leave it."""

import base64
import hashlib
import hmac
import re
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.contracts.source import (
    MaterializationSourceFile,
    MaterializationSourceResult,
)
from packages.policies.repository import allowed_entry, safe_tree_path
from services.auth.access import AuthAccess
from services.auth.github import AuthDenied
from services.context.selection import select_paths
from services.orchestrator.collection import (
    CollectionInputs,
    InputLoader,
    SourceInputError,
    run_collection,
)
from services.orchestrator.source import github_inputs
from services.orchestrator.worker import WorkerResult, bind_incident
from services.persistence.store import Lease, Store, StoreError
from services.repository.github import RepositoryReadError
from services.repository.snapshot import SnapshotError

LoaderFactory = Callable[[SecretStr, tuple[str, ...]], InputLoader]


class StageRequest(BaseModel):
    """Independent workload authentication is mandatory in addition to this fence."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    lease: Lease = Field(repr=False)
    expected_version: int = Field(strict=True, ge=0)


class CollectionRequest(StageRequest):
    """Collection adds selected source paths to the shared current-job fence."""

    paths: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("paths")
    @classmethod
    def approved_shape(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(paths)) != len(paths) or any(not safe_tree_path(p) for p in paths):
            raise ValueError("COLLECTION_PATH_INVALID")
        return paths


class CollectionBroker:
    """Trusted local service: independent worker key + current DB fence + consent.

    Only collection runs here. No inference, shell, sandbox dispatch or publication.
    Production calls are separately opt-in; test factories cannot be installed in
    production. Paths narrow the existing grant before any blob download.
    """

    def __init__(
        self,
        store: Store,
        access: AuthAccess,
        worker_key: SecretStr,
        *,
        allow_live: bool = False,
        source_loader_factory: LoaderFactory | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", worker_key.get_secret_value()):
            raise ValueError("COLLECTION_WORKER_KEY_INVALID")
        if source_loader_factory is not None and not access.allow_test:
            raise ValueError("COLLECTION_TEST_ADAPTER_DENIED")
        self.store, self.access, self._key = store, access, worker_key
        self._live = allow_live is True
        self._factory = source_loader_factory or github_inputs
        self._test = source_loader_factory is not None

    def authenticate(self, request: Request) -> None:
        provided = request.headers.get("authorization", "")
        if (
            request.headers.get("origin") is not None
            or request.headers.get("sec-fetch-site") is not None
            or not hmac.compare_digest(
                provided.encode(), ("Bearer " + self._key.get_secret_value()).encode()
            )
        ):
            raise HTTPException(403, "COLLECTION_WORKLOAD_DENIED")

    def process(self, payload: CollectionRequest) -> WorkerResult:
        if not self._live and not self._test:
            raise HTTPException(403, "COLLECTION_LIVE_NOT_AUTHORIZED")
        owner, state = self.store.claimed_run(payload.lease)
        if state.version != payload.expected_version:
            raise StoreError("STALE_VERSION")
        if state.cancellation_requested or state.current_stage not in {
            Stage.CREATED,
            Stage.VALIDATING,
            Stage.QUEUED,
            Stage.COLLECTING,
        }:
            raise StoreError("COLLECTION_STAGE_DENIED")
        self.access.authorize(owner, state.repository_id)
        grant = self.store.repository(state.repository_id, owner)
        if any(not allowed_entry(grant, path, "100644") for path in payload.paths):
            raise HTTPException(403, "COLLECTION_PATH_DENIED")
        report = self.store.incident(state.run_id, owner)
        paths = select_paths(grant, report.text, payload.paths)
        token = self.access.journal.credential(owner)

        # The source adapter narrows file reads without rewriting the registered grant.
        def source(run: RunState, admitted_grant: RepositoryGrant) -> CollectionInputs:
            self.access.authorize(owner, run.repository_id)
            # Recheck lease/version/cancellation immediately before source I/O.
            _, current = self.store.claimed_run(payload.lease)
            if current.version != run.version or current.cancellation_requested:
                raise StoreError("STALE_VERSION")
            return self._factory(token, paths)(run, admitted_grant)

        result = run_collection(
            self.store,
            owner,
            payload.lease,
            input_loader=bind_incident(self.store, source),
            queue_triage=True,
        )
        return WorkerResult(
            status="PROCESSED", run_id=result.run_id, stage=result.current_stage
        )

    def authorize_triage(self, payload: StageRequest) -> dict[str, object]:
        """Fresh identity check only; no inference, token or reusable grant returned."""
        if not self._live and not self._test:
            raise HTTPException(403, "COLLECTION_LIVE_NOT_AUTHORIZED")
        owner, state = self.store.claimed_run(payload.lease)
        if (
            state.version != payload.expected_version
            or state.current_stage != Stage.TRIAGING
            or state.cancellation_requested
        ):
            raise StoreError("TRIAGE_STAGE_DENIED")
        self.access.authorize(owner, state.repository_id)
        return {"run_id": state.run_id, "version": state.version, "status": "ADMITTED"}

    def authorize_patch(self, payload: StageRequest) -> dict[str, object]:
        """Current repository membership only; no new identity/approval authority."""
        if not self._live and not self._test:
            raise HTTPException(403, "COLLECTION_LIVE_NOT_AUTHORIZED")
        owner, state = self.store.claimed_run(payload.lease)
        if (
            state.version != payload.expected_version
            or state.current_stage != Stage.PATCHING
            or state.cancellation_requested
        ):
            raise StoreError("PATCH_STAGE_DENIED")
        self.access.authorize(owner, state.repository_id)
        return {"run_id": state.run_id, "version": state.version, "status": "ADMITTED"}

    def materialization_source(
        self, payload: StageRequest
    ) -> MaterializationSourceResult:
        """Fetch the pinned manifest subset after fresh membership/lease checks.

        Raw source bytes cross only this authenticated loopback worker boundary.
        Paths come from the retained source manifest, never from worker/model text.
        """
        if not self._live and not self._test:
            raise HTTPException(403, "COLLECTION_LIVE_NOT_AUTHORIZED")
        owner, state = self.store.claimed_run(payload.lease)
        if (
            state.version != payload.expected_version
            or state.current_stage not in {Stage.REPRODUCING, Stage.VERIFYING}
            or state.cancellation_requested
            or state.source_manifest_ref is None
        ):
            raise StoreError("MATERIALIZATION_SOURCE_STAGE_DENIED")
        self.access.authorize(owner, state.repository_id)
        grant = self.store.repository(state.repository_id, owner)
        manifest = self.store.source_manifest(
            state.run_id, owner, state.source_manifest_ref
        )
        paths = tuple(item.path for item in manifest.files)
        if (
            not 1 <= len(paths) <= 16
            or any(not allowed_entry(grant, path, "100644") for path in paths)
            or sum(item.size_bytes for item in manifest.files) > 65536
        ):
            raise HTTPException(403, "MATERIALIZATION_SOURCE_SCOPE_DENIED")
        token = self.access.journal.credential(owner)
        # Recheck current identity and lease immediately before fetching pinned
        # provider content. The loader validates commit/tree and Git blob IDs.
        self.access.authorize(owner, state.repository_id)
        _, current = self.store.claimed_run(payload.lease)
        if (
            current.version != state.version
            or current.current_stage != state.current_stage
            or current.cancellation_requested
        ):
            raise StoreError("STALE_VERSION")
        try:
            inputs = self._factory(token, paths)(state, grant)
        except (
            SourceInputError,
            RepositoryReadError,
            SnapshotError,
            OSError,
            TimeoutError,
        ):
            raise HTTPException(503, "MATERIALIZATION_SOURCE_UNAVAILABLE") from None
        fresh = inputs.source_manifest
        if (
            fresh is None
            or fresh.repository_id != manifest.repository_id
            or fresh.source_commit != manifest.source_commit
            or fresh.root_tree != manifest.root_tree
            or fresh.files != manifest.files
            or inputs.snapshot.source_commit != state.source_commit
            or tuple(item.path for item in inputs.snapshot.files) != paths
        ):
            raise HTTPException(409, "MATERIALIZATION_SOURCE_CHANGED")
        files: list[MaterializationSourceFile] = []
        total = 0
        for item in manifest.files:
            body = inputs.snapshot.read_file(item.path, 65536)
            blob_id = hashlib.sha1(
                f"blob {len(body)}\0".encode() + body, usedforsecurity=False
            ).hexdigest()
            if (
                len(body) != item.size_bytes
                or hashlib.sha256(body).hexdigest() != item.sha256
                or blob_id != item.blob_id
            ):
                raise HTTPException(409, "MATERIALIZATION_SOURCE_CHANGED")
            total += len(body)
            if total > 65536:
                raise HTTPException(413, "MATERIALIZATION_SOURCE_LIMIT")
            files.append(
                MaterializationSourceFile(
                    path=item.path,
                    content_b64=base64.b64encode(body).decode("ascii"),
                )
            )
        return MaterializationSourceResult(
            run_id=state.run_id,
            version=state.version,
            source_commit=state.source_commit,
            manifest_ref=state.source_manifest_ref,
            files=tuple(files),
        )

    def authorize_materialization_source(
        self, payload: StageRequest
    ) -> dict[str, object]:
        """Recheck live identity, consent and lease without transferring bytes."""
        if not self._live and not self._test:
            raise HTTPException(403, "COLLECTION_LIVE_NOT_AUTHORIZED")
        owner, state = self.store.claimed_run(payload.lease)
        if (
            state.version != payload.expected_version
            or state.current_stage not in {Stage.REPRODUCING, Stage.VERIFYING}
            or state.cancellation_requested
            or state.source_manifest_ref is None
        ):
            raise StoreError("MATERIALIZATION_SOURCE_STAGE_DENIED")
        self.access.authorize(owner, state.repository_id)
        return {"run_id": state.run_id, "version": state.version, "status": "ADMITTED"}


def install_routes(app: FastAPI, broker: CollectionBroker) -> None:
    """Explicit internal route, excluded from the browser/model bridge vocabulary."""

    @app.get("/internal/v1/collection/ready")
    def ready(request: Request) -> dict[str, bool]:
        broker.authenticate(request)
        return {"enabled": broker._live or broker._test}

    @app.post("/internal/v1/collection")
    def process(payload: CollectionRequest, request: Request) -> WorkerResult:
        broker.authenticate(request)
        try:
            return broker.process(payload)
        except AuthDenied:
            broker.access.journal.denial("AUTH_COLLECTION_DENIED")
            raise HTTPException(403, "COLLECTION_REPOSITORY_DENIED") from None

    @app.post("/internal/v1/collection/authorize-triage")
    def authorize_triage(payload: StageRequest, request: Request) -> dict[str, object]:
        broker.authenticate(request)
        try:
            return broker.authorize_triage(payload)
        except AuthDenied:
            broker.access.journal.denial("AUTH_TRIAGE_DENIED")
            raise HTTPException(403, "TRIAGE_REPOSITORY_DENIED") from None

    @app.post("/internal/v1/collection/authorize-patch")
    def authorize_patch(payload: StageRequest, request: Request) -> dict[str, object]:
        broker.authenticate(request)
        try:
            return broker.authorize_patch(payload)
        except AuthDenied:
            broker.access.journal.denial("AUTH_PATCH_DENIED")
            raise HTTPException(403, "PATCH_REPOSITORY_DENIED") from None

    @app.post("/internal/v1/collection/materialization-source")
    def materialization_source(
        payload: StageRequest, request: Request
    ) -> MaterializationSourceResult:
        broker.authenticate(request)
        try:
            return broker.materialization_source(payload)
        except AuthDenied:
            broker.access.journal.denial("AUTH_MATERIALIZATION_SOURCE_DENIED")
            raise HTTPException(
                403, "MATERIALIZATION_SOURCE_REPOSITORY_DENIED"
            ) from None

    @app.post("/internal/v1/collection/authorize-materialization-source")
    def authorize_materialization_source(
        payload: StageRequest, request: Request
    ) -> dict[str, object]:
        broker.authenticate(request)
        try:
            return broker.authorize_materialization_source(payload)
        except AuthDenied:
            broker.access.journal.denial("AUTH_MATERIALIZATION_SOURCE_DENIED")
            raise HTTPException(
                403, "MATERIALIZATION_SOURCE_REPOSITORY_DENIED"
            ) from None
