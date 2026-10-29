"""M4 benchmark accounting; it aggregates observations but authenticates none.

This module never runs fixture code and cannot authorize a gate or change a run.
Remote trust must be established by the execution and verification pipeline
before its records are suitable for a production claim.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.contracts.run import ArtifactID

Availability = Literal["AVAILABLE", "UNAVAILABLE"]
Origin = Literal["REMOTE_SERVICE", "TEST_TRANSPORT", "LOCAL_STATIC"]
AttemptStatus = Literal["VERIFIED", "REJECTED", "FAILED", "INCONCLUSIVE", "BLOCKED"]
OracleStatus = Literal["PASS", "FAIL", "UNAVAILABLE"]
ReproductionStatus = Literal["PASS", "FAIL", "UNKNOWN"]


class BenchmarkCase(BaseModel):
    """One declared evaluation case; unavailable fixtures remain visible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    availability: Availability
    unavailable_reason: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def require_unavailable_reason(self) -> BenchmarkCase:
        if (self.availability == "UNAVAILABLE") != bool(self.unavailable_reason):
            raise ValueError("CASE_AVAILABILITY_REASON_INVALID")
        return self


class BenchmarkAttempt(BaseModel):
    """A sanitized observed attempt; fields are not proof of trusted execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    origin: Origin
    status: AttemptStatus
    attempted: bool = True
    trace_id: UUID | None = None
    reproduction: ReproductionStatus = "UNKNOWN"
    oracle_status: OracleStatus = "UNAVAILABLE"
    failure_category: str | None = Field(
        default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$"
    )
    evidence_refs: tuple[ArtifactID, ...] = Field(default=(), max_length=32)
    model: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, max_length=120)
    provider_estimated_cost_usd: float | None = Field(default=None, ge=0)
    latency_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_claims(self) -> BenchmarkAttempt:
        if self.attempted and self.trace_id is None:
            raise ValueError("EVALUATION_TRACE_ID_REQUIRED")
        if not self.attempted and self.status not in {"BLOCKED", "INCONCLUSIVE"}:
            raise ValueError("UNATTEMPTED_CASE_STATUS_INVALID")
        if self.status == "VERIFIED":
            if (
                self.origin != "REMOTE_SERVICE"
                or self.oracle_status not in {"PASS", "FAIL"}
                or len(self.evidence_refs) < 2
            ):
                raise ValueError("VERIFIED_EVALUATION_BINDING_REQUIRED")
        if not self.attempted and self.origin != "LOCAL_STATIC":
            raise ValueError("UNATTEMPTED_ORIGIN_INVALID")
        if self.provider_estimated_cost_usd is not None and not math.isfinite(
            self.provider_estimated_cost_usd
        ):
            raise ValueError("EVALUATION_COST_INVALID")
        if self.latency_seconds is not None and not math.isfinite(self.latency_seconds):
            raise ValueError("EVALUATION_LATENCY_INVALID")
        return self


class CaseSummary(BaseModel):
    """Counts for one inventory entry with exact denominators."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    fixture_id: str
    available: bool
    unavailable_reason: str | None
    attempted_cases: int = Field(ge=0)
    live_attempts: int = Field(ge=0)
    test_transport_attempts: int = Field(ge=0)
    local_static_attempts: int = Field(ge=0)
    verified_oracle_correct: int = Field(ge=0)
    false_verified_count: int = Field(ge=0)
    inconclusive_count: int = Field(ge=0)
    failure_categories: dict[str, int]


class BenchmarkTotals(BaseModel):
    """Aggregate M4 counts and rates; rates are null for zero denominators."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    available_cases: int = Field(ge=0)
    unavailable_cases: int = Field(ge=0)
    attempted_cases: int = Field(ge=0)
    live_attempts: int = Field(ge=0)
    test_transport_attempts: int = Field(ge=0)
    local_static_attempts: int = Field(ge=0)
    reproduced_baselines: int = Field(ge=0)
    verified_oracle_correct: int = Field(ge=0)
    false_verified_count: int = Field(ge=0)
    inconclusive_count: int = Field(ge=0)
    patch_scope_rejections: int = Field(ge=0)
    reproduction_validity_rate: float | None
    verified_patch_rate: float | None
    inconclusive_rate: float | None
    total_provider_estimated_cost_usd: float | None
    median_latency_seconds: float | None
    p95_latency_seconds: float | None
    latency_sample_count: int = Field(ge=0)


class BenchmarkReport(BaseModel):
    """Untrusted aggregate, never a gate producer or live-evidence verifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["replayforge.m4-evaluation-report.v2"] = (
        "replayforge.m4-evaluation-report.v2"
    )
    evidence_authority: Literal["UNAUTHENTICATED_AGGREGATE"] = (
        "UNAUTHENTICATED_AGGREGATE"
    )
    cases: tuple[CaseSummary, ...]
    attempts: tuple[BenchmarkAttempt, ...]
    totals: BenchmarkTotals


def summarize_benchmark(
    inventory: tuple[BenchmarkCase, ...], attempts: tuple[BenchmarkAttempt, ...]
) -> BenchmarkReport:
    """Summarize supplied rows without upgrading them to trusted evidence."""
    if not inventory or len({case.case_id for case in inventory}) != len(inventory):
        raise ValueError("CASE_INVENTORY_INVALID")
    by_case = {case.case_id: case for case in inventory}
    grouped: dict[str, list[BenchmarkAttempt]] = {
        case.case_id: [] for case in inventory
    }
    for attempt in attempts:
        case = by_case.get(attempt.case_id)
        if case is None:
            raise ValueError("CASE_NOT_IN_INVENTORY")
        if case.availability == "UNAVAILABLE":
            raise ValueError("ATTEMPT_FOR_UNAVAILABLE_CASE")
        grouped[attempt.case_id].append(attempt)

    summaries: list[CaseSummary] = []
    for case in inventory:
        rows = grouped[case.case_id]
        actual = [row for row in rows if row.attempted]
        failures = Counter(
            row.failure_category for row in actual if row.failure_category is not None
        )
        summaries.append(
            CaseSummary(
                case_id=case.case_id,
                fixture_id=case.fixture_id,
                available=case.availability == "AVAILABLE",
                unavailable_reason=case.unavailable_reason,
                attempted_cases=len(actual),
                live_attempts=sum(row.origin == "REMOTE_SERVICE" for row in actual),
                test_transport_attempts=sum(
                    row.origin == "TEST_TRANSPORT" for row in actual
                ),
                local_static_attempts=sum(
                    row.origin == "LOCAL_STATIC" for row in actual
                ),
                verified_oracle_correct=sum(
                    row.status == "VERIFIED" and row.oracle_status == "PASS"
                    for row in actual
                ),
                false_verified_count=sum(
                    row.status == "VERIFIED" and row.oracle_status == "FAIL"
                    for row in actual
                ),
                inconclusive_count=sum(row.status == "INCONCLUSIVE" for row in rows),
                failure_categories=dict(sorted(failures.items())),
            )
        )

    actual_rows = [row for row in attempts if row.attempted]
    attempted_count = len(actual_rows)
    latencies = sorted(
        row.latency_seconds for row in actual_rows if row.latency_seconds is not None
    )
    p95 = (
        latencies[math.ceil(0.95 * len(latencies)) - 1]
        if len(latencies) >= 20
        else None
    )
    cost_values = [
        row.provider_estimated_cost_usd
        for row in actual_rows
        if row.origin == "REMOTE_SERVICE"
    ]
    verified_correct = sum(case.verified_oracle_correct for case in summaries)
    reproduced = sum(row.reproduction == "PASS" for row in actual_rows)
    return BenchmarkReport(
        cases=tuple(summaries),
        attempts=attempts,
        totals=BenchmarkTotals(
            available_cases=sum(case.availability == "AVAILABLE" for case in inventory),
            unavailable_cases=sum(
                case.availability == "UNAVAILABLE" for case in inventory
            ),
            attempted_cases=attempted_count,
            live_attempts=sum(row.origin == "REMOTE_SERVICE" for row in actual_rows),
            test_transport_attempts=sum(
                row.origin == "TEST_TRANSPORT" for row in actual_rows
            ),
            local_static_attempts=sum(
                row.origin == "LOCAL_STATIC" for row in actual_rows
            ),
            reproduced_baselines=reproduced,
            verified_oracle_correct=verified_correct,
            false_verified_count=sum(case.false_verified_count for case in summaries),
            inconclusive_count=sum(case.inconclusive_count for case in summaries),
            patch_scope_rejections=sum(
                row.failure_category == "PATCH_SCOPE" for row in actual_rows
            ),
            reproduction_validity_rate=(
                reproduced / attempted_count if attempted_count else None
            ),
            verified_patch_rate=(
                verified_correct / attempted_count if attempted_count else None
            ),
            inconclusive_rate=(
                sum(case.inconclusive_count for case in summaries) / attempted_count
                if attempted_count
                else None
            ),
            total_provider_estimated_cost_usd=(
                sum(value for value in cost_values if value is not None)
                if cost_values and all(value is not None for value in cost_values)
                else None
            ),
            median_latency_seconds=median(latencies) if latencies else None,
            p95_latency_seconds=p95,
            latency_sample_count=len(latencies),
        ),
    )
