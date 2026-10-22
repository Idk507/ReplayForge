"""Bounded collection/triage harness over existing durable stage controllers."""

import math
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.inference import ModelCostPolicy
from packages.providers.nebius import NebiusConfig
from services.orchestrator.collection import InputLoader
from services.orchestrator.worker import StopSignal, tick, tick_triage
from services.persistence.store import Store


class HarnessResult(BaseModel):
    """Scheduling statistics, never a repair verdict or execution permission."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    steps: int = Field(ge=0)
    processed: int = Field(ge=0)
    collected: int = Field(ge=0)
    triaged: int = Field(ge=0)
    idle: int = Field(ge=0)
    inference_enabled: bool
    stop_requested: bool


def serve_harness(
    store: Store,
    worker_id: UUID,
    input_loader: InputLoader,
    stop: StopSignal,
    *,
    model_config: NebiusConfig | None = None,
    cost_policy: ModelCostPolicy | None = None,
    allow_live_inference: bool = False,
    transport: httpx.BaseTransport | None = None,
    max_steps: int = 100,
    idle_seconds: float = 1,
) -> HarnessResult:
    """Alternate fixed stage queues; use durable journals rather than chat state.

    One step is at most one stage claim. No model chooses a stage/tool or writes
    a checkpoint here. Failed stages propagate without retry; existing sticky
    intents and fences handle restarts. Collection queues triage even when
    inference is disabled. Later execution/approval stages are not dispatched.
    """
    if (
        type(max_steps) is not int
        or not 1 <= max_steps <= 1000
        or type(idle_seconds) not in (int, float)
        or not math.isfinite(idle_seconds)
        or not 0 < idle_seconds <= 60
    ):
        raise ValueError("HARNESS_LIMIT")
    if (model_config is None) != (cost_policy is None):
        raise ValueError("HARNESS_MODEL_POLICY_REQUIRED")
    if model_config is not None and cost_policy is not None:
        model_config = NebiusConfig.from_env(
            {
                "NEBIUS_API_KEY": model_config.api_key.get_secret_value(),
                "NEBIUS_BASE_URL": model_config.base_url,
                "NEBIUS_MODEL_PRIMARY": model_config.model,
            }
        )
        cost_policy = ModelCostPolicy.model_validate(cost_policy.model_dump())
        if (
            model_config.model != cost_policy.model
            or model_config.base_url != cost_policy.base_url
        ):
            raise ValueError("HARNESS_MODEL_POLICY_MISMATCH")
        if transport is None and allow_live_inference is not True:
            raise ValueError("HARNESS_LIVE_NOT_AUTHORIZED")
    enabled = model_config is not None
    next_triage = enabled
    steps = collected = triaged = idle = consecutive_idle = 0
    while steps < max_steps and not stop.is_set():
        if next_triage and model_config is not None and cost_policy is not None:
            result = tick_triage(
                store, worker_id, model_config, cost_policy, transport=transport
            )
        else:
            result = tick(store, worker_id, input_loader, queue_triage=True)
        steps += 1
        if result.status == "PROCESSED":
            triaged += int(next_triage)
            collected += int(not next_triage)
            consecutive_idle = 0
        else:
            idle += 1
            consecutive_idle += 1
        next_triage = not next_triage if enabled else False
        if consecutive_idle >= (2 if enabled else 1) and steps < max_steps:
            if stop.wait(idle_seconds):
                break
            consecutive_idle = 0
    return HarnessResult(
        steps=steps,
        processed=collected + triaged,
        collected=collected,
        triaged=triaged,
        idle=idle,
        inference_enabled=enabled,
        stop_requested=stop.is_set(),
    )
