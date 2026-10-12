"""Versioned research contracts. Retrieved claims never authorize execution."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ResearchContract(BaseModel):
    """Reject unrestricted model text and extra instruction-bearing fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class DocumentationQuery(ResearchContract):
    """Only registered package/topic/version choices may leave the platform."""

    package: Literal[
        "python", "pytest", "fastapi", "pandas", "sqlalchemy", "postgresql"
    ]
    topic: Literal["transactions", "idempotency", "concurrency", "migration", "testing"]
    version: str | None = Field(
        default=None, pattern=r"^[0-9]{1,3}(?:\.[0-9]{1,3}){0,2}$", max_length=11
    )
    max_results: int = Field(default=5, ge=1, le=5, strict=True)


class ResearchSource(ResearchContract):
    """Sanitized advisory text with original URL and local retrieval timestamp."""

    url: str
    title: str = Field(max_length=240)
    snippet: str = Field(max_length=1000)
    retrieved_at: datetime
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    truncated: bool
    trust: Literal["UNTRUSTED_EXTERNAL"] = "UNTRUSTED_EXTERNAL"
    can_authorize_execution: Literal[False] = False


class ResearchReport(ResearchContract):
    """Safe broker observation, distinct from reproduction/verification evidence."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["SUCCESS", "NOT_RUN", "FAILED", "INCONCLUSIVE"]
    evidence_origin: Literal["NOT_RUN", "TEST_TRANSPORT", "REMOTE_SERVICE"]
    observed_at: datetime
    trace_id: UUID
    policy_decision: Literal[
        "ALLOW_REGISTERED_DOC_SEARCH",
        "DENY_DISABLED",
        "DENY_LIVE_NOT_AUTHORIZED",
        "DENY_BUDGET",
        "DENY_QUERY",
        "DENY_CLOSED",
    ]
    error_class: str | None = None
    retryable: Literal[False] = False
    sources: tuple[ResearchSource, ...] = ()
    artifacts: tuple[str, ...] = ()
    omitted_sources: int = Field(default=0, ge=0)
    searches_remaining: int = Field(ge=0, le=2)
    usage_credits: int | None = Field(default=None, ge=0)
    estimated_cost_usd: None = None
    latency_ms: float = Field(default=0, ge=0)
