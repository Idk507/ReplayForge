# ReplayForge — Product Scope and PRD

## Vision
Turn actionable Python service incidents into verifiable, human-reviewed code changes. The customer is an engineer or small incident-response team with a trusted GitHub repository, not an autonomous production deployment system. ReplayForge minimizes time between a reproducible failure and a safe reviewable patch. Winning theme: *from incident to evidence, not incident to speculation*.

## Problem statement and persona
Traditional triage crosses issue trackers, logs, code, dependency documentation and tests. LLM assistants often generate plausible patches without reproducing the original failure or confirming safety. Persona A: backend engineer handling a duplicate-payment retry. Persona B: data engineer repairing silent aggregation drift. Persona C: reviewer who needs understandable proof. Demo judge: can trigger and inspect real run without providing personal credentials.

## Jobs to be done
An authorized engineer submits incident context and a pinned repository. The platform investigates and reproduces failure, proposes a minimal patch, tests it in a fresh isolated environment, emits an evidence report, awaits explicit review and only then opens a PR. The engineer can inspect why an attempt was rejected and rerun with a new hypothesis. The demo makes it easy to compare failures and fixes.

## MVP functional requirements
FR-01 authorization and repository scope registration. FR-02 issue/log ingestion and redaction. FR-03 pinned commit snapshot and relevant context retrieval. FR-04 Nemotron inference through Nebius Token Factory at runtime. FR-05 typed LangGraph workflow and persisted events. FR-06 failing regression generation and baseline failure observation. FR-07 isolated execution of allowed tests. FR-08 unified diff generation/validation. FR-09 candidate verification and fresh clean replay. FR-10 explicit terminal verdict with artifact lineage. FR-11 OTel trace, cost and latency recording. FR-12 responsive UI with incident detail, timeline, diff, tests and provenance. FR-13 human approval bound to patch/source and GitHub PR creation. FR-14 Tavily search of relevant upstream docs with citations when useful. FR-15 replayable public demo fixtures.

## Three gold-path scenarios
A: FastAPI checkout retry with duplicate charge due to missing durable idempotency. Expect baseline duplicate, patched single logical charge, parallel and sequential retry safety, database-backed test fixture. B: ETL pipeline revenue grouping error after schema field update, detect silent totals mismatch; known fixture expected aggregates. C: concurrency counter race; force deterministic scheduling barriers instead of relying solely on flaky timing; expect invariant-preserving patch. Each has separate hidden oracle tests unavailable to agent. At minimum A is complete first; B and C are stretch targets until the first end-to-end flow is operational.

## Nonfunctional requirements
Security: repo consent, tenant isolation, no arbitrary host execution, no secrets in LLM input, narrow GitHub privileges, auditable approvals. Reliability: durable run resumption, idempotent external effects, bounded costs/retries, deterministic verdict. Performance: stage telemetry, usable streaming updates, explicit budget caps rather than promised absolute repair latency. Scalability: stateless APIs, limited concurrency workers, sandbox capacity admission, storage lifecycle. UX: understandable errors, accessibility, mobile-readable summary and desktop diff. Maintainability: ports/adapters, strict types, contract tests and up-to-date docs.

## Explicit exclusions until after Oct 30
Automatic production deployment, pull request merge, unrestricted browser/computer use, support for arbitrary languages, arbitrary private repositories, remote debugging of customer production environments, third-party observability connectors, unrestricted patch files, long-term semantic memory, RALPH-style infinite loops, autonomous fleet orchestration, training/fine-tuning large models, independent cybersecurity vulnerability exploitation and broad Internet execution access. Do not accidentally imply these are implemented.

## Product screens
Home: 'Run demonstration' and recent runs; intake: repo/fixture, incident text, permitted branch, consent; run: stage timeline, live events, inference/tool cost; investigation: hypothesis/evidence/source references; reproduction: original test failure and signature; patch: side-by-side diff and change explanation; verification: gate matrix, clean replay, inconclusive reasons; review: audited human approval and PR link; evaluation: aggregate benchmark performance and failures. Demo public viewer is read-only; authenticated operator performs writes.

## Success measurements
Primary: verified repairs over attempted incidents with independent oracle correctness. Secondary: reproduction validity, false VERIFIED count (target zero), complete evidence manifests, reviewer time, mean/median inference cost, repair latency, test regression rate and sandbox policy violations (target zero). Never report model's confidence as probability of correctness. Track failed and inconclusive attempts in published benchmark.

## Release gates
M0: user can run Nemotron smoke test against actual Nebius credentials and identify available model. M1: deterministic run with synthetic fixture and persisted events. M2: actual baseline fail + actual candidate pass in an isolated sandbox. M3: full clean replay and evidence gate. M4: authenticated approval creates one GitHub PR. M5: polished 3-minute real app video, public repo/license/README, deployed working demo, evaluation and Devpost submission. Every gate needs observable proof.

## Commercial extension (not an MVP promise)
GitHub/GitLab enterprise installations, Sentry/Datadog/OpenTelemetry incident sources, hosted sandbox pools, organization policies, branch protection orchestration, usage-based billing, multi-repository dependency tracing, additional programming languages, post-merge feedback and continual benchmark improvement. Evaluate licensing, security reviews, software liability and compliance before market launch.

## Competition compliance
Official submission deadline Oct 30 2026 at 10:00 PDT (22:30 IST). Coding and Agentic Engineering track. Runnable application must call Token Factory at runtime and use NVIDIA open model; public repository with OSI license; working demo URL; public YouTube video shorter than 3 minutes; technology feedback and text description. Verify Devpost status manually before deadline; judging is Dec 1–15, so keep the demonstration available through judging. Follow rules and no guarantees of prizes.
