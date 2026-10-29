"""M4 evaluation accounting never promotes synthetic rows to live proof."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from services.runner.benchmark_evaluation import (
    BenchmarkAttempt,
    BenchmarkCase,
    summarize_benchmark,
)


def inventory() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            case_id="checkout-idempotency",
            fixture_id="checkout_retry",
            availability="AVAILABLE",
        ),
        BenchmarkCase(
            case_id="etl-aggregation-drift",
            fixture_id="etl_aggregation_drift",
            availability="UNAVAILABLE",
            unavailable_reason=(
                "No fixture or source commit is present in benchmarks/fixtures."
            ),
        ),
    )


def test_empty_evaluation_has_zero_denominators_and_no_rates():
    report = summarize_benchmark(inventory(), ())

    assert report.totals.attempted_cases == 0
    assert report.totals.live_attempts == 0
    assert report.totals.verified_patch_rate is None
    assert report.cases[0].available is True
    assert report.cases[1].available is False


def test_synthetic_rejection_is_separate_from_live_attempts():
    attempt = BenchmarkAttempt(
        case_id="checkout-idempotency",
        origin="TEST_TRANSPORT",
        status="REJECTED",
        failure_category="PATCH_SCOPE",
        trace_id=UUID("00000000-0000-4000-8000-000000000001"),
        evidence_refs=("sha256:" + "a" * 64,),
    )

    report = summarize_benchmark(inventory(), (attempt,))

    assert report.totals.attempted_cases == 1
    assert report.totals.test_transport_attempts == 1
    assert report.totals.live_attempts == 0
    assert report.totals.patch_scope_rejections == 1
    assert report.evidence_authority == "UNAUTHENTICATED_AGGREGATE"
    assert report.attempts[0].trace_id == UUID("00000000-0000-4000-8000-000000000001")


def test_test_transport_cannot_be_reported_as_verified():
    with pytest.raises(ValidationError):
        BenchmarkAttempt(
            case_id="checkout-idempotency",
            origin="TEST_TRANSPORT",
            status="VERIFIED",
            oracle_status="PASS",
            trace_id=UUID("00000000-0000-4000-8000-000000000002"),
            evidence_refs=("sha256:" + "a" * 64, "sha256:" + "b" * 64),
        )


def test_verified_claim_requires_separate_gate_and_oracle_refs():
    with pytest.raises(ValidationError):
        BenchmarkAttempt(
            case_id="checkout-idempotency",
            origin="REMOTE_SERVICE",
            status="VERIFIED",
            oracle_status="PASS",
            trace_id=UUID("00000000-0000-4000-8000-000000000003"),
            evidence_refs=("sha256:" + "a" * 64,),
        )


def test_unknown_case_and_duplicate_inventory_ids_are_rejected():
    attempt = BenchmarkAttempt(
        case_id="not-in-inventory",
        origin="TEST_TRANSPORT",
        status="BLOCKED",
        trace_id=UUID("00000000-0000-4000-8000-000000000004"),
        failure_category="RUNTIME_UNAVAILABLE",
    )
    with pytest.raises(ValueError, match="CASE_NOT_IN_INVENTORY"):
        summarize_benchmark(inventory(), (attempt,))
    duplicate = inventory()[0]
    with pytest.raises(ValueError, match="CASE_INVENTORY_INVALID"):
        summarize_benchmark((duplicate, duplicate), ())


def test_live_oracle_results_keep_exact_denominators_and_false_verified_count():
    refs = ("sha256:" + "a" * 64, "sha256:" + "b" * 64)
    attempts = (
        BenchmarkAttempt(
            case_id="checkout-idempotency",
            origin="REMOTE_SERVICE",
            status="VERIFIED",
            reproduction="PASS",
            oracle_status="PASS",
            trace_id=UUID("00000000-0000-4000-8000-000000000005"),
            evidence_refs=refs,
            provider_estimated_cost_usd=0.05,
            latency_seconds=10,
        ),
        BenchmarkAttempt(
            case_id="checkout-idempotency",
            origin="REMOTE_SERVICE",
            status="VERIFIED",
            reproduction="FAIL",
            oracle_status="FAIL",
            trace_id=UUID("00000000-0000-4000-8000-000000000006"),
            evidence_refs=refs,
            provider_estimated_cost_usd=0.07,
            latency_seconds=20,
        ),
    )

    totals = summarize_benchmark(inventory(), attempts).totals

    assert totals.attempted_cases == 2
    assert totals.live_attempts == 2
    assert totals.verified_oracle_correct == 1
    assert totals.false_verified_count == 1
    assert totals.reproduction_validity_rate == 0.5
    assert totals.verified_patch_rate == 0.5
    assert totals.total_provider_estimated_cost_usd == pytest.approx(0.12)
    assert totals.median_latency_seconds == 15
    assert totals.p95_latency_seconds is None
    assert [
        row.trace_id for row in summarize_benchmark(inventory(), attempts).attempts
    ] == [
        UUID("00000000-0000-4000-8000-000000000005"),
        UUID("00000000-0000-4000-8000-000000000006"),
    ]


def test_unavailable_fixture_cannot_receive_attempt_rows():
    case = inventory()[1]
    attempt = BenchmarkAttempt(
        case_id=case.case_id,
        origin="TEST_TRANSPORT",
        status="FAILED",
        trace_id=UUID("00000000-0000-4000-8000-000000000007"),
        failure_category="FIXTURE_NOT_AVAILABLE",
    )
    with pytest.raises(ValueError, match="ATTEMPT_FOR_UNAVAILABLE_CASE"):
        summarize_benchmark(inventory(), (attempt,))


def test_attempt_trace_and_measurement_details_are_retained_in_report():
    trace_id = UUID("00000000-0000-4000-8000-000000000008")
    attempt = BenchmarkAttempt(
        case_id="checkout-idempotency",
        origin="REMOTE_SERVICE",
        status="FAILED",
        trace_id=trace_id,
        failure_category="BASELINE_NOT_REPRODUCED",
        provider_estimated_cost_usd=0.17,
        latency_seconds=42.5,
    )

    report = summarize_benchmark(inventory(), (attempt,))

    assert report.attempts == (attempt,)
    assert report.attempts[0].trace_id == trace_id
    assert report.attempts[0].failure_category == "BASELINE_NOT_REPRODUCED"
    assert report.attempts[0].provider_estimated_cost_usd == 0.17
    assert report.attempts[0].latency_seconds == 42.5


def test_attempt_without_trace_id_is_rejected():
    with pytest.raises(ValidationError):
        BenchmarkAttempt(
            case_id="checkout-idempotency",
            origin="TEST_TRANSPORT",
            status="REJECTED",
            failure_category="PATCH_SCOPE",
        )
