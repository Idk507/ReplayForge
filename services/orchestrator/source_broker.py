"""Credential-isolated client for one loopback collection broker dispatch."""

import base64
import binascii
import hashlib
import re
import time
from types import TracebackType
from typing import Literal, Self
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from packages.contracts.inference import ModelCostPolicy
from packages.contracts.run import Stage
from packages.contracts.source import MaterializationSourceResult, SourceManifest
from packages.providers.nebius import NebiusConfig
from services.orchestrator.patch_worker import tick_patch
from services.orchestrator.repair_flow import run_triage
from services.orchestrator.worker import WorkerResult
from services.persistence.store import Lease, Store
from services.runner.receipts import canonical


class BrokerError(Exception):
    """Closed workload failure; no provider bodies, keys or lease capabilities."""


class Readiness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class TriageAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    version: int
    status: Literal["ADMITTED"]


class BrokerClient:
    def __init__(
        self,
        base_url: str,
        key: SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not re.fullmatch(
            r"http://127\.0\.0\.1:[0-9]{1,5}", base_url
        ) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", key.get_secret_value()):
            raise ValueError("BROKER_CONFIG_INVALID")
        if not 1 <= int(base_url.rsplit(":", 1)[1]) <= 65535:
            raise ValueError("BROKER_CONFIG_INVALID")
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(60, connect=3),
            headers={
                "Authorization": "Bearer " + key.get_secret_value(),
                "Accept-Encoding": "identity",
            },
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        typ: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        max_bytes: int = 4096,
    ) -> bytes:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 131072:
            raise BrokerError("BROKER_LIMIT_INVALID")
        deadline = time.monotonic() + 65
        try:
            with self._client.stream(method, path, json=body) as response:
                if response.status_code != 200:
                    raise BrokerError(
                        "COLLECTION_DENIED"
                        if response.status_code in {401, 403, 404, 409, 422}
                        else "COLLECTION_UNCONFIRMED"
                    )
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    if (
                        len(data) + len(chunk) > max_bytes
                        or time.monotonic() >= deadline
                    ):
                        raise BrokerError("COLLECTION_UNCONFIRMED")
                    data.extend(chunk)
                return bytes(data)
        except httpx.HTTPError:
            raise BrokerError("COLLECTION_UNCONFIRMED") from None

    def ready(self) -> None:
        try:
            ready = Readiness.model_validate_json(
                self._request("GET", "/internal/v1/collection/ready")
            )
        except ValueError:
            raise BrokerError("BROKER_NOT_READY") from None
        if not ready.enabled:
            raise BrokerError("BROKER_NOT_READY")

    def dispatch(
        self, lease: Lease, expected_version: int, paths: tuple[str, ...]
    ) -> WorkerResult:
        try:
            result = WorkerResult.model_validate_json(
                self._request(
                    "POST",
                    "/internal/v1/collection",
                    {
                        "lease": lease.model_dump(mode="json"),
                        "expected_version": expected_version,
                        "paths": list(paths),
                    },
                )
            )
        except ValueError:
            raise BrokerError("COLLECTION_UNCONFIRMED") from None
        if (
            result.status != "PROCESSED"
            or result.run_id != lease.run_id
            or result.stage
            not in {
                Stage.TRIAGING,
                Stage.INCONCLUSIVE,
                Stage.POLICY_BLOCKED,
                Stage.CANCELLED,
            }
        ):
            raise BrokerError("COLLECTION_UNCONFIRMED")
        return result

    def authorize_triage(self, lease: Lease, version: int) -> None:
        try:
            admitted = TriageAdmission.model_validate_json(
                self._request(
                    "POST",
                    "/internal/v1/collection/authorize-triage",
                    {
                        "lease": lease.model_dump(mode="json"),
                        "expected_version": version,
                    },
                )
            )
        except ValueError:
            raise BrokerError("TRIAGE_ADMISSION_UNCONFIRMED") from None
        if admitted.run_id != lease.run_id or admitted.version != version:
            raise BrokerError("TRIAGE_ADMISSION_UNCONFIRMED")

    def authorize_patch(self, lease: Lease, version: int) -> None:
        try:
            admitted = TriageAdmission.model_validate_json(
                self._request(
                    "POST",
                    "/internal/v1/collection/authorize-patch",
                    {
                        "lease": lease.model_dump(mode="json"),
                        "expected_version": version,
                    },
                )
            )
        except ValueError:
            raise BrokerError("PATCH_ADMISSION_UNCONFIRMED") from None
        if admitted.run_id != lease.run_id or admitted.version != version:
            raise BrokerError("PATCH_ADMISSION_UNCONFIRMED")

    def authorize_materialization_source(self, lease: Lease, version: int) -> None:
        """Freshly authorize pinned source read/runner I/O without returning data."""
        try:
            admitted = TriageAdmission.model_validate_json(
                self._request(
                    "POST",
                    "/internal/v1/collection/authorize-materialization-source",
                    {
                        "lease": lease.model_dump(mode="json"),
                        "expected_version": version,
                    },
                )
            )
        except ValueError:
            raise BrokerError("MATERIALIZATION_ADMISSION_UNCONFIRMED") from None
        if admitted.run_id != lease.run_id or admitted.version != version:
            raise BrokerError("MATERIALIZATION_ADMISSION_UNCONFIRMED")

    def fetch_materialization_source(
        self, lease: Lease, version: int, manifest: SourceManifest
    ) -> dict[str, bytes]:
        """Fetch raw source only from the auth broker's exact current manifest."""
        try:
            response = MaterializationSourceResult.model_validate_json(
                self._request(
                    "POST",
                    "/internal/v1/collection/materialization-source",
                    {
                        "lease": lease.model_dump(mode="json"),
                        "expected_version": version,
                    },
                    max_bytes=131072,
                )
            )
        except ValueError:
            raise BrokerError("MATERIALIZATION_SOURCE_UNCONFIRMED") from None
        if (
            response.run_id != lease.run_id
            or response.version != version
            or response.source_commit != manifest.source_commit
            or response.manifest_ref
            != "sha256:" + hashlib.sha256(canonical(manifest)).hexdigest()
            or tuple(item.path for item in response.files)
            != tuple(item.path for item in manifest.files)
        ):
            raise BrokerError("MATERIALIZATION_SOURCE_UNCONFIRMED")
        files: dict[str, bytes] = {}
        total = 0
        try:
            for transfer, expected in zip(response.files, manifest.files, strict=True):
                body = base64.b64decode(transfer.content_b64, validate=True)
                blob = hashlib.sha1(
                    f"blob {len(body)}\0".encode() + body, usedforsecurity=False
                ).hexdigest()
                if (
                    transfer.path != expected.path
                    or len(body) != expected.size_bytes
                    or hashlib.sha256(body).hexdigest() != expected.sha256
                    or blob != expected.blob_id
                ):
                    raise BrokerError("MATERIALIZATION_SOURCE_UNCONFIRMED")
                total += len(body)
                if total > 65536:
                    raise BrokerError("MATERIALIZATION_SOURCE_UNCONFIRMED")
                files[transfer.path] = body
        except (binascii.Error, ValueError):
            raise BrokerError("MATERIALIZATION_SOURCE_UNCONFIRMED") from None
        return files


def dispatch_once(
    store: Store, worker_id: UUID, client: BrokerClient, paths: tuple[str, ...]
) -> WorkerResult:
    """Probe readiness before claim. One POST only; never replay an unknown result."""
    client.ready()
    lease = store.claim(
        worker_id,
        stages=(Stage.CREATED, Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING),
    )
    if lease is None:
        return WorkerResult(status="IDLE")
    state_owner, state = store.claimed_run(lease)
    result = client.dispatch(lease, state.version, paths)
    persisted = store.get(lease.run_id, state_owner)
    if (
        persisted.current_stage != result.stage
        or persisted.source_commit != state.source_commit
        or persisted.repository_id != state.repository_id
    ):
        raise BrokerError("COLLECTION_UNCONFIRMED")
    return result


def dispatch_triage_once(
    store: Store,
    worker_id: UUID,
    client: BrokerClient,
    config: NebiusConfig,
    cost_policy: ModelCostPolicy,
    *,
    transport: httpx.BaseTransport | None = None,
) -> WorkerResult:
    """Claim triage only; authenticate access before the existing fenced journal.

    Internal caller must authorize inference. The gateway reserves actual request
    identity/budget before POST; unknown results remain sticky, never retried here.
    No GitHub credentials or shell tools reach this worker.
    """
    client.ready()
    lease = store.claim(worker_id, triage_only=True)
    if lease is None:
        return WorkerResult(status="IDLE")
    owner, state = store.claimed_run(lease)
    client.authorize_triage(lease, state.version)
    result = run_triage(
        store,
        lease,
        owner,
        config,
        cost_policy=cost_policy,
        transport=transport,
        finish_job=True,
        queue_baseline=True,
    )
    return WorkerResult(
        status="PROCESSED", run_id=result.run_id, stage=result.current_stage
    )


def dispatch_patch_once(
    store: Store,
    worker_id: UUID,
    client: BrokerClient,
    config: NebiusConfig,
    cost_policy: ModelCostPolicy,
    *,
    transport: httpx.BaseTransport | None = None,
) -> WorkerResult:
    """Fresh broker membership check, then one fenced inference/queue handoff."""
    client.ready()
    return tick_patch(
        store,
        worker_id,
        config,
        cost_policy,
        allow_inference=True,
        transport=transport,
        authorize=client.authorize_patch,
    )
