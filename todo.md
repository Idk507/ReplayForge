# ReplayForge — Delivery Tracker

**Planning date:** Monday 12 Oct 2026 | **Hard deadline:** Fri 30 Oct 2026, 10:00 PDT / 22:30 IST. All boxes initially unchecked: no implementation or live test is asserted by this documentation package.

## Status conventions
`[ ]` not evidenced; `[x]` verified by committed change and test artifact; `[!]` blocked; `[~]` working but not acceptance-ready (replace symbols manually when appropriate). A task is complete only when its acceptance test has been actually run and evidence logged in `docs/implementation-log.md`. Track owner, commit, observed execution date and blocker there. Critical path is M0 -> M1 -> M2 -> M3 -> M4 -> M5.

## M0 — Day 1–2, Oct 12–13: Platform viability (stop/go)
- [~] First code unit: bounded Nebius catalog/completion probe, offline negative tests and Windows setup. See implementation log; live inference, CI and clean-clone acceptance remain pending. Changes are local and uncommitted.
- [ ] Register on Devpost; confirm eligibility and rules. Acceptance: project draft exists in account.
- [ ] Apply Nebius program credits; verify account key is private. Acceptance: console or API usage visible, never print key.
- [~] Live catalog + one Nemotron completion passed on 2026-10-15 (HTTP 200, 22 input / 27 output tokens). Sanitized report and trace: m0-live-inference-attempt.json. Cost unknown; provider request ID not captured; merge/complete M0 acceptance pending.
- [~] One live synthetic sandbox probe passed on 2026-10-15: Python 3.12.13, UID/GID 1000, loopback only, no credential environment, correlated terminal compute confirmed (m0-live-sandbox-attempt.json). CPU/memory/PID/read-only-root and deletion/retention guarantees remain unverified; arbitrary repository execution stays disabled.
- [ ] Verify Tavily key or SDK access for actual runtime call. Acceptance: search returns source URLs.
- [~] Authorized live README fetch passed through ReplayForge for Idk507/maf_ds_agent at 08d828835fcb0cada16aca45c721ad0075e0c70e. CLI access metadata reports admin/push capabilities; separate read-only installation and exact token scopes remain pending. See m1-live-github-checks.json.
- [ ] Create repo skeleton and OSS license. Acceptance: clean clone and CI smoke.
**Gate:** stop broad implementation if no viable Nemotron inference or isolation design is available; resolve blockers first.

Local evidence update (2026-10-12): the fixed remote sandbox probe, typed
contracts, cancellation handling and offline negative tests are implemented.
The observed suite is 66 passing tests, with lint, format, strict types, lock
and wheel build checks passing. See `docs/implementation-log.md` and
`docs/sandbox-smoke.md`. No M0 live acceptance box is complete: the sandbox
CLI reports missing configuration, and no cloud execution was authorized or
performed. The probe supports trusted synthetic inputs only; production runner
capabilities remain unverified. Changes are local and uncommitted.

Additional local evidence (2026-10-12): curated Tavily documentation queries,
sanitized source contracts and an offline-first smoke CLI are implemented.
The full suite now has 110 passing tests (44 new research tests); lint, format,
strict types, lock validation, wheel build and isolated imports passed. See
`docs/tavily-research.md` and `docs/evidence/m0-tavily-tests.xml`. Live Tavily
access and M3 authentic agent-path acceptance remain unverified. No live M0
checkbox is completed by these offline tests.

## M1 — Oct 14–16: Control plane + agent loop
- [~] Pydantic schemas and enums: local RunState, stages, artifact references and action budgets validated; full agent/API contracts and durable integration remain pending. See implementation log.
- [~] Postgres/Alembic checkpoint, event outbox and fenced job leases: real local process-death, rollback, concurrency and recovery tests passed. Three expired collection claims now atomically close the run as INCONCLUSIVE with LEASE_EXHAUSTED; cancellation and audit rollback tested. Full domain schema, later-stage failure routing and merged acceptance remain pending. See m1-exhaustion-checks.json.
- [~] FastAPI run creation, GET and cancellation: loopback-only bearer authentication, repository UUID scope and real DB tests passed. Redacted incident reports now commit atomically with intake; owner, checksum, expiry, idempotency and rollback tested. Repository consent resolution, rate limiting and production auth remain pending. See m1-incident-checks.json.
- [~] Finite SSE replay pages: Last-Event-ID reconnect returns unseen persisted events in real DB/API tests. Continuous streaming/UI reconnect and deployment acceptance remain pending.
- [~] GitHub pinned snapshot: bounded acquisition and provenance tests pass; authorized live README fetch/hash passed on a pinned maf_ds_agent commit (m1-live-github-checks.json). Excluded secret files are omitted without blob fetch while content admission remains denied. Opt-in encrypted source subsets now pass round-trip, binding, tamper, owner, expiry and transactional PostgreSQL tests (m1-encrypted-snapshot-checks.json). Bounded active/historical key rotation and restart reconstruction now pass tests (m1-key-rotation-checks.json). Production key provisioning/recovery, complete replay packaging, least-privilege installation and full live worker acceptance remain pending.
- [~] Redactor/context collector: sanitized bundles persist atomically with hashed checkpoint references; owner/expiry/checksum API reads and rollback tested with PostgreSQL. Bounded approved-root dependency identities and aggregate digest now persist with source-manifest binding; restart and legacy-artifact tests passed (m1-dependency-provenance-checks.json: 377 tests, 57 PostgreSQL cases). Discovery, complete retention and later-agent graph integration remain pending.
- [~] LangGraph collection recovery, scoped loader and bounded polling tested with PostgreSQL. Metadata source manifests now commit atomically with context/checkpoint/job completion and expose owner-scoped verified reads. Polling has bounded ticks, idle waits, cooperative stop and no automatic exception retry (m1-durable-worker-checks.json). Opt-in sealed snapshot storage and keyring loader integration passed within 371 tests, including 55 PostgreSQL scenarios (m1-key-rotation-checks.json). Explicit offline-default worker CLI, loopback runtime-role guard and cooperative signals now pass startup/stop/failure PostgreSQL tests (m1-worker-startup-checks.json: 391 tests, 61 PostgreSQL cases). Production key provisioning/recovery, purge, service supervision, full graph/live worker acceptance and action reconciliation remain pending.
**Gate:** fixture run reaches TRIAGING with durable trace and controlled outcome.

## M2 — Oct 17–20: Real execution and repair
- [~] SandboxBackend remains fixed M0-probe-only. Signed runner/image/runtime-configuration capability gate now passes 22-control missing/failure/unknown checks, limits, forgery, origin, freshness and configuration-drift tests (m2-runner-admission-checks.json: 631 tests, 93 PostgreSQL cases). No execution permission is issued. Production assessor/key, real capability evidence, complete remote adapter and API/metadata denial acceptance remain pending.
- [~] Fixed pytest-file command planning and typed bindings now pass offline injection/scope/artifact/stage/budget tests (m2-command-plan-checks.json: 417 tests total). Plans cannot authorize execution. Strict modify-only Python unified-diff admission now passes source/baseline/test binding, path, format and size negatives (m2-patch-admission-checks.json: 447 tests, 61 PostgreSQL cases). Remote dispatch, git apply --check/application and executable evidence remain pending; atomic baseline reservations are evidenced below.
- [ ] Checkout checkout-retry benchmark and record hidden oracle. Acceptance: bug demonstrably fails before intervention.
- [ ] Nemotron hypothesis/repro generation with schema validation. Acceptance: real inference creates a runnable test candidate.
- [~] Bounded unauthenticated JUnit inspection now rejects setup/import/syntax/skip/count/hash/target mismatches and qualifies an exact assertion marker (m2-baseline-inspection-checks.json: 469 tests, 61 PostgreSQL cases). Explicit Ed25519 receipt signature/correlation and actual-report binding now pass offline forgery/freshness/execution/tree/origin negatives (m2-runner-receipt-checks.json: 491 tests). A match still cannot authorize PATCHING; Append-only baseline action/receipt journal now passes restart, concurrent replay, owner/lease/version, retention, rollback and exhausted-budget evidence tests (m2-baseline-journal-checks.json: 502 tests, 71 PostgreSQL cases). Atomic baseline intent + one-command debit, version increment and budget audit now pass concurrent replay, rollback, owner, cancellation, lease, version, budget and completed-action tests (m2-dispatch-reservation-checks.json: 509 tests, 78 PostgreSQL cases). Bounded unverified provider-operation recovery metadata now passes restart, concurrent replay, unique operation binding, owner/lease/version/cancellation, zero-budget, rollback, permission and corruption checks (m2-operation-journal-checks.json: 534 tests, 93 PostgreSQL cases). No external dispatch is enabled. Production signer, live provider lookup/polling and submission controller, reducer acceptance, live runner admission and genuine baseline failure remain pending.
- [ ] Generate patch, apply in fresh checkout, run regression. Acceptance: actual patch and actual test results.
- [ ] Add max attempts, spend and timeout limits. Acceptance: budget exhaustion closes run.
**Gate:** actual buggy fixture transitions through reproduced failure into candidate tested patch.

## M3 — Oct 21–23: Verification and experience
- [ ] Clean replay in independent fresh runner. Acceptance: image/source/test digests recorded.
- [ ] Implement mandatory deterministic gate evaluator. Acceptance: fabricated model claim cannot set VERIFIED.
- [ ] Manifest hashing, artifact access control and retention. Acceptance: tampering fails verification.
- [ ] Tavily documentation retrieval in at least one authentic agent path. Acceptance: retrieved citations appear in investigation timeline.
- [ ] Next.js dashboard, intake, run timeline, diff and evidence matrix. Acceptance: real run displayed without fake statuses.
- [ ] SSE reconnect and accessible errors. Acceptance: disconnect/reconnect retains sequence.
**Gate:** judge can inspect a complete verified or rejected outcome in UI.

## M4 — Oct 24–26: External write and benchmark
- [ ] Approval bound to immutable patch digest and reviewer authorization. Acceptance: changed patch invalidates approval.
- [ ] One-use GitHub PR publisher. Acceptance: retry creates exactly one PR.
- [ ] Incident B aggregation fixture. Acceptance: hidden oracle identifies truth.
- [ ] Incident C concurrency fixture. Acceptance: repeated deterministic race reproduction.
- [ ] Run holdout mutation trials; publish actual denominator, successes, failures and inconclusive cases. Acceptance: machine-readable results checked into benchmarks output.
- [ ] Security test suite: prompt injection, repo traversal, malicious patch, Tavily poison, SSRF, runner escape policy. Acceptance: zero accepted forbidden actions.
- [ ] Trace, token cost, error dashboards. Acceptance: single trace correlates incident to artifact.
**Gate:** one fully approved PR and evaluation report; at least one unsuccessful/inconclusive case shown honestly.

## M5 — Oct 27–29: Submission
- [ ] Deploy with public stable URL and demo-only safe accounts. Acceptance: independent browser can complete approved demo flow.
- [ ] Public repository with license, reproducible setup, architecture and limitations. Acceptance: clean-clone test.
- [ ] Record sub-3-minute public YouTube demo with authentic evidence. Acceptance: playback and visibility verified in anonymous browser.
- [ ] Draft Devpost description: inspiration, what it does, how built, challenges, accomplishments, learned, future work, NVIDIA/Nebius/Tavily usage. Acceptance: all required fields complete.
- [ ] Verify track, working hosted URL, open-source repo, NVIDIA runtime call, feedback and YouTube URL. Acceptance: checklist screenshot or documented manual audit.
- [ ] Submit by Oct 29 with one-day margin. Acceptance: Devpost submission shows completed status and public project.
- [ ] Establish health checks and budget to remain online through Dec 15 judging. Acceptance: uptime/credit watchdog configured.
**Gate:** publicly accessible, compliant, tested submission.

## Kill-switch and contingency decisions
If runner isolation is not ready by Oct 17, reduce scope to a *trusted synthetic benchmark-only* remote runner and explicitly constrain accepted repositories to fixed fixtures; never fall back to arbitrary local execution. If reproduction cannot be achieved by Oct 20, freeze UI work and concentrate on one verified checkout case. If GitHub write permission cannot be safely obtained by Oct 25, ship reviewable patch artifacts with a clearly marked PR integration limitation; do not claim full feature completion. If Nebius credits are low, disable speculative multi-agent calls, use one supported NVIDIA model and cached deterministic fixtures; live user actions must still call Token Factory. If B/C are unstable by Oct 26, demonstrate A thoroughly and publish truthful benchmark gaps.

## Final acceptance ledger template
| Gate | Expected evidence | Observed result | Commit / artifact | Date |
|---|---|---|---|---|
| Live NVIDIA on Nebius | request ID + model + timestamp | Pending | — | — |
| Sandbox isolated | policy negative test + runner log | Pending | — | — |
| Baseline reproduced | original failure signature | Pending | — | — |
| Patched clean replay | regression and existing suite | Pending | — | — |
| Approval-gated PR | exact digest + PR URL | Pending | — | — |
| Deployment accessible | public health/demo screenshot | Pending | — | — |
| Devpost submission | public submission URL | Pending | — | — |
