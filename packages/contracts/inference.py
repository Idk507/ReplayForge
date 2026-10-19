"""Safe request identity and operator-declared cost ceiling, without prompts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.contracts.run import ArtifactID


class RequestIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    request_ref: ArtifactID
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=256)
    timeout_seconds: Literal[30] = 30
    max_completion_tokens: Literal[1200] = 1200


class ModelCostPolicy(BaseModel):
    """An operator estimate, not verified pricing or a provider billing cap."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=256)
    policy_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    estimated_spend_microusd: int = Field(strict=True, ge=1, le=2_000_000)


class ModelDispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    request: RequestIdentity
    cost: ModelCostPolicy

    @model_validator(mode="after")
    def matching_policy(self) -> Self:
        if (
            self.request.model != self.cost.model
            or self.request.base_url != self.cost.base_url
        ):
            raise ValueError("MODEL_COST_POLICY_MISMATCH")
        return self
