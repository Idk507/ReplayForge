# Evaluation Plan and Benchmark Protocol

## Evaluation questions
Can the system (1) reproduce a real bug on baseline, (2) generate a patch passing independent hidden correctness checks, (3) avoid regressions, (4) refuse insufficient or unsafe evidence, (5) finish within practical runtime/cost and (6) maintain provenance?

## Dataset construction
Three first-party fixtures: checkout idempotency, ETL aggregation drift and concurrency race. Each has a pinned base commit, issue prompt, baseline test, independently hidden oracle tests, known expected invariants, environment image digest and setup instructions. Create 10–15 holdout variants via renamed files/functions, changed test inputs, altered irrelevant logs and randomized stable seeded fixtures; isolate oracle tests from model context. The evaluation harness should not allow the agent to read the benchmark answer implementation.

## Experimental arms
A: one-pass Nemotron code patch without reproduction-driven loop. B: ReplayForge full baseline/repro/patch/clean replay workflow. Run each condition with recorded seed, same model availability and comparable spend budgets. Model sampling need not be fully deterministic; report the variance and configuration. Optionally add C: ReplayForge without Tavily on documentation-sensitive cases to estimate value of research. Do not infer significance from 3 demos alone.

## Metrics
`reproduction_validity = valid_target_reproductions / attempted_cases`; `verified_patch_rate = independently_oracle_correct_verified_patches / attempted_cases`; `false_verified_count = cases_marked_VERIFIED_but_hidden_oracle_failed`; `inconclusive_rate = inconclusive / attempted_cases`; `regression_rate = patches_failing_original_existing_tests / attempted_patches`; `cost_per_case = sum(provider_estimated_cost)/attempts`; `median_latency_seconds`, p95 when sample size supports it; `policy_violation_count`; `artifact_completeness`. Report numerator/denominator explicitly and separate agent verification from hidden-oracle correctness. Show failure traces and spend estimates based on real usage.

## Test hierarchy
Unit tests for model/tool state; integration for live adapters and isolated runner; E2E for seeded three fixtures; adversarial policy suite; clean replay repeated twice for flaky cases. Test only reproducible semantic failure signature as baseline; syntactic/import faults do not count. Use independent test runner image and oracle for benchmark result. Store JSONL run rows with repo SHA, model, provider, attempt count, verdict, oracle score, resource usage, timestamp, trace and redacted artifact refs.

## Reporting integrity
No invented percentages or artificial '100%'. If only one fixture works, show one achieved and two incomplete. If live provider is unavailable, record blocked, not mocked. Include failure examples, hidden-oracle deviations and confidence intervals only when statistically meaningful. Publish script, fixture licenses, seeds and exact commands for independent reproduction.

## Current M4 inventory and safe accounting

`benchmarks/m4-evaluation-input.json` is the current declarative inventory.
Only `checkout_retry` is present. ETL aggregation drift, concurrency race, and
the seeded hidden-variation sample are explicitly unavailable because their
fixture/source assets are absent. The attempt list is empty: this checkout
cannot be executed on the Windows API host, and no qualified remote runtime is
available. Therefore no fixture outcome, hidden-oracle result, success rate, or
cost-per-case is reported.

The bounded accounting utility validates rows without executing fixture code:

```powershell
uv run python scripts/report_m4_evaluation.py
uv run pytest -q tests/unit/test_benchmark_evaluation.py
```

Its JSON has `evidence_authority=UNAUTHENTICATED_AGGREGATE`; it is transparent
accounting input, not a trusted verifier, execution receipt, or production
benchmark result. The current observed output is retained in
`docs/evidence/m4-evaluation-status.json` and explained in
`docs/evidence/m4-evaluation-status.md`. A synthetic PATCH_SCOPE rejection test
is separately retained at `docs/evidence/m4-rejected-candidate-policy.xml`.
That policy test proves only local admission behavior; it is not a live rejected
repair demonstration.
