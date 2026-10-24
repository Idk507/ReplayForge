"""Credential-isolated client for one loopback collection broker dispatch."""

import re
import time
from types import TracebackType
from typing import Literal, Self
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from packages.contracts.inference import ModelCostPolicy
from packages.contracts.run import Stage
from packages.providers.nebius import NebiusConfig
from services.orchestrator.repair_flow import run_triage
from services.orchestrator.worker import WorkerResult
from services.persistence.store import Lease, Store


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
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> bytes:
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
                    if len(data) + len(chunk) > 4096 or time.monotonic() >= deadline:
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
