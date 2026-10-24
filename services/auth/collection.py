"""Auth-service collection boundary; GitHub/vault credentials never leave it."""

import hmac
import re
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import allowed_entry, safe_tree_path
from services.auth.access import AuthAccess
from services.auth.github import AuthDenied
from services.orchestrator.collection import (
    CollectionInputs,
    InputLoader,
    run_collection,
)
from services.orchestrator.source import github_inputs
from services.orchestrator.worker import WorkerResult, bind_incident
from services.persistence.store import Lease, Store, StoreError

LoaderFactory = Callable[[SecretStr, tuple[str, ...]], InputLoader]


class CollectionRequest(BaseModel):
    """Independent workload authentication is mandatory in addition to this fence."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    lease: Lease = Field(repr=False)
    expected_version: int = Field(strict=True, ge=0)
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
        token = self.access.journal.credential(owner)

        # The source adapter narrows file reads without rewriting the registered grant.
        def source(run: RunState, admitted_grant: RepositoryGrant) -> CollectionInputs:
            self.access.authorize(owner, run.repository_id)
            # Recheck lease/version/cancellation immediately before source I/O.
            _, current = self.store.claimed_run(payload.lease)
            if current.version != run.version or current.cancellation_requested:
                raise StoreError("STALE_VERSION")
            return self._factory(token, payload.paths)(run, admitted_grant)

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
