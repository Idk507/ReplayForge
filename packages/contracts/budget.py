"""Bounded remaining action counters; cost/time reservations are not implemented."""

from pydantic import BaseModel, ConfigDict, Field


class Budget(BaseModel):
    """Fail-closed default action ceilings for one run checkpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    model_calls: int = Field(default=15, strict=True, ge=0, le=15)
    sandbox_commands: int = Field(default=20, strict=True, ge=0, le=20)
    research_searches: int = Field(default=2, strict=True, ge=0, le=2)
    patch_attempts: int = Field(default=3, strict=True, ge=0, le=3)
