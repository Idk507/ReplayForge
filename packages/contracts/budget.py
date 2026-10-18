"""Bounded remaining action, active-time and estimated-spend budget."""

from pydantic import BaseModel, ConfigDict, Field


class Budget(BaseModel):
    """Fail-closed default action ceilings for one run checkpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    model_calls: int = Field(default=15, strict=True, ge=0, le=15)
    sandbox_commands: int = Field(default=20, strict=True, ge=0, le=20)
    research_searches: int = Field(default=2, strict=True, ge=0, le=2)
    patch_attempts: int = Field(default=3, strict=True, ge=0, le=3)
    # Wall-clock reservation is charged before remote dispatch. This bounds the
    # total active execution requested by a run, independently of command count.
    execution_seconds: int = Field(default=900, strict=True, ge=0, le=900)
    # USD micro-units avoid floating-point currency accounting. A provider action
    # must have a configured upper-bound estimate before it can debit this field.
    estimated_spend_microusd: int = Field(
        default=2_000_000, strict=True, ge=0, le=2_000_000
    )
