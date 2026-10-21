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

**Written milestone exit: COMPLETE for the existing fixed-checkout contingency
(2026-10-19).** Every criterion in milestones.md's M2 acceptance section is
mapped to observed evidence in `docs/evidence/m2-final-exit.json`. The current
exit run passed 18 tests with zero skips and verified committed live-artifact
checksums. This does **not** close the broader production implementation rows
below, authorize arbitrary repositories, or issue VERIFIED. Proceed to M3
evidence presentation/trust work within the explicit fixture boundary.

- [~] SandboxBackend supports the fixed probe and separate fixed-checkout contingency runner; real inferred baseline, Git-applied candidate and clean replay have passed. The general repository capability gate still requires 22 assessed controls (m2-runner-admission-checks.json). Production assessor/key, real capability evidence, complete general adapter and API/metadata denial acceptance remain pending.
- [~] Fixed pytest-file command planning and typed bindings now pass offline injection/scope/artifact/stage/budget tests (m2-command-plan-checks.json: 417 tests total). Plans cannot authorize execution. Strict modify-only Python unified-diff admission now passes source/baseline/test binding, path, format and size negatives (m2-patch-admission-checks.json: 447 tests, 61 PostgreSQL cases). Remote dispatch, git apply --check/application and executable evidence remain pending; atomic baseline reservations are evidenced below.
- [x] Fixed checkout baseline: the real inferred regression failed for duplicate charging on the original fixture commit in a remote sandbox, with no setup error. Original source/tree and unchanged test identities are captured in m2-checkout-e2e/report.json and committed in 7cf4349. Hidden-oracle evaluation is not claimed.
- [x] Nemotron hypothesis/repro generation with schema validation: real inference generated the runnable checkout regression, which failed for the intended duplicate-charge assertion remotely. Real inferred patch applied with git apply and passed in two fresh executions with unchanged test/source identities (2026-10-19, m2-checkout-e2e/report.json; committed 7cf4349). This is fixture acceptance, not a production VERIFIED verdict.
- [~] Bounded unauthenticated JUnit inspection now rejects setup/import/syntax/skip/count/hash/target mismatches and qualifies an exact assertion marker (m2-baseline-inspection-checks.json: 469 tests, 61 PostgreSQL cases). Explicit Ed25519 receipt signature/correlation and actual-report binding now pass offline forgery/freshness/execution/tree/origin negatives (m2-runner-receipt-checks.json: 491 tests). A match still cannot authorize PATCHING; Append-only baseline action/receipt journal now passes restart, concurrent replay, owner/lease/version, retention, rollback and exhausted-budget evidence tests (m2-baseline-journal-checks.json: 502 tests, 71 PostgreSQL cases). Atomic baseline intent + one-command debit, version increment and budget audit now pass concurrent replay, rollback, owner, cancellation, lease, version, budget and completed-action tests (m2-dispatch-reservation-checks.json: 509 tests, 78 PostgreSQL cases). Bounded unverified provider-operation recovery metadata now passes restart, concurrent replay, unique operation binding, owner/lease/version/cancellation, zero-budget, rollback, permission and corruption checks (m2-operation-journal-checks.json: 534 tests, 93 PostgreSQL cases). No external dispatch is enabled. Production signer, live provider lookup/polling and submission controller, reducer acceptance, live runner admission and genuine baseline failure remain pending.
- [x] Fixed checkout inferred candidate: allowed-path diff checked/applied with git, then passed the unchanged regression in separate candidate and clean-replay sandboxes; tree identities match. Committed patch/test/report artifacts: 7cf4349, m2-checkout-e2e/. Scope is the fixed visible fixture; hidden-oracle/holdout acceptance is not claimed.
- [~] Action ceilings plus durable 900-second active-time and $2 estimated-spend budgets validate and fail closed. Exhaustion atomically records BUDGET_EXHAUSTED, transitions INCONCLUSIVE and closes the lease (historical m2-budget-exhaustion-checks.json: 638 tests). Model request/operator-estimate/configured-timeout reservation is now evidenced below. Actual provider pricing/usage, absolute-time reconciliation and sandbox dispatch accounting remain unverified; this budget layer does not authorize external actions.
- [x] Request-bound production-controller inference reservations: exact outbound body SHA256, model/endpoint, 1200-token profile, configured timeout and operator cost declaration now commit with the existing sticky intent and atomic call/time/estimate debits before provider submission (3c8e2b3). Missing/mismatched policy prevents submission; exhaustion closes INCONCLUSIVE; concurrency, rollback, failure/restart and oversized-response negatives pass. Evidence: m2-model-budget-final-postgres.xml, 765 passed, zero skips. This is configured-timeout accounting and an operator estimate, not absolute wall-time enforcement or verified billing.
- [x] Known-operation read-only recovery controller: owner-scoped journal ID, active lease and version fencing bind one bounded REST GET; terminal states append transactionally, restart skips HTTP, and unknown submission never resubmits. Commit `b4c9b9b`; `m2-operation-recovery-postgres.xml`: 788 passed, zero skips, 148.40 seconds, exit 0. HTTP is synthetic in these tests; live production admission, submission, signed report delivery and polling-worker scheduling remain pending. See `docs/operation-recovery.md`.
**Gate:** actual buggy fixture transitions through reproduced failure into candidate tested patch.

- [x] Restart-safe signed baseline delivery (`6ee1a71`): migration 0015 retains bounded JUnit bytes atomically with the authenticated receipt; owner/expiry/digest checks guard private reads. Delivery resumes after acceptance rollback, reconciles concurrent completion, rejects conflicting redelivery and reuses the existing authenticated reducer. Evidence: `m2-baseline-delivery-postgres.xml`, **802 passed, zero skips**, 132.64 seconds, exit 0; Ruff lint/format and strict mypy passed. Synthetic signing authority tests contracts only; production collector/signing and assessed runner integration remain open. See `docs/baseline-delivery.md`.

- [x] Opt-in durable collection-to-triage worker handoff (`7c43858`): context/checkpoint and next-stage queue commit atomically; stage-scoped claims isolate collection from triage; successful reproduction retention closes its job atomically. Uncertain requests cannot repeat after restart. Evidence: `m2-triage-worker-postgres.xml`, **791 passed, zero skips**, 138.43 seconds, exit 0; Ruff lint/format and strict mypy passed. Inference uses synthetic HTTP in these tests. Default collection remains inference-off; remaining remote execution and full graph acceptance stay open. See `docs/triage-worker.md`.

Fixture-contingency gate observed on 2026-10-19: generated regression baseline
exit 1, inferred Git-applied candidate exit 0, independent clean replay exit 0.
All three use the same source commit and test digest; candidate/replay tree
hashes match. See `docs/m2-checkout-acceptance.md` and
`docs/evidence/m2-checkout-e2e/report.json`. Path/command denials prevent network
submission and emit sanitized audit records. This completes the standalone
fixed-fixture M2 exit demonstration, not general repository or durable worker
acceptance. Production capability/signing evidence and graph integration remain
open; no VERIFIED verdict or PR authorization is issued.
Final full local suite with disposable PostgreSQL: 718 passed, zero skips,
one upstream warning (`m2-checkout-postgres-tests.xml`); lint, format and strict
types passed. At that observation the new work was uncommitted; the closure
commits below now retain the fixture implementation and its artifacts.

Closure update (2026-10-19): `uv run python -m scripts.check_checkout` now
independently checks the saved real fixture artifacts and returns
`M2_FIXTURE_ACCEPTED`, exit 0, without cloud use. Digest/source/image/tree and
operation-ID tampering, absent tests, setup faults and terminal-state changes
are rejected. The CLI produces INCONCLUSIVE for invalid generated syntax before
sandbox dispatch. Transactional baseline acceptance and its continuation into
the one-shot patch controller are implemented; reserved, freshly authenticated
remote evidence is required, and invalid reproduction closes INCONCLUSIVE.
No synthetic contract signature is claimed as live production evidence.

**Current boundary:** the fixed-fixture M2 exit criteria pass. Full production
M2 remains open: actual capability/signing evidence, generic remote dispatch,
its polling-worker integration, and actual model usage/elapsed-time reconciliation
are not provided by the fixture contingency. Production-controller request and
declared-budget binding is evidenced below. Do not promote remaining items to complete
or manufacture a production VERIFIED verdict. The next deployable/demo work
is M3 evidence presentation; the next general-runner prerequisite is a trusted
assessed runner/collector configuration.

Closure validation: **746 passed, zero skips**, one upstream warning, 92.23s
(`m2-closure-exit-handling-postgres.xml`); Ruff lint/format and strict mypy passed.
Local commits: `15d131e` (durable baseline reduction/continuation and actual
process-exit classification), `7cf4349` (fixture admission/integrity checks and
captured live evidence). No push, merge, deployment or cloud call this attempt.

Subsequent M2 validation (2026-10-19): **765 passed, zero skips**, one upstream
warning, 100.07s (`m2-model-budget-final-postgres.xml`), Ruff lint/format and
strict mypy passed. Commit `3c8e2b3` closes the local model request/declared-cost/
configured-timeout reservation gap; migration 0013 is required for the new
intent metadata. No cloud calls or remote writes occurred. Actual elapsed-time
and usage reconciliation, live pricing, assessed runner capabilities/signing,
general remote dispatch and its polling-worker integration remain open.

Local M2 persistence evidence (2026-10-18): one-shot patch inference reservation
and admitted-candidate retention now pass real disposable PostgreSQL restart,
duplicate denial, ownership, rollback and migration checks within 708 passing
tests (`m2-patch-journal-final-tests.xml`). This does not complete the gate:
model request/spend/time binding, controller integration, admitted remote patch
application and the correlated baseline/candidate replay remain pending.

### Baseline timeout reservation evidence — 2026-10-19

- [x] Reserve the baseline command's full planned timeout atomically with its command count. Commit `6f18eb2`; migration 0014 records `reserved_seconds`, preserving historical unknown values as NULL. Insufficient time closes INCONCLUSIVE without a partial debit or dispatch intent; duplicate reconciliation does not debit twice, and audit failure rolls back.

Validation: `m2-sandbox-time-postgres.xml` records **769 passed, zero skips**,
one upstream warning, 105.71 seconds, exit 0. Focused checks: 29 passed;
pre-fix regression: 3 failed (`m2-sandbox-time-red.xml.gz`, lossless capture).
Ruff lint/format (146 files), strict mypy (77 files), diff checks and targeted
credential-value scanning passed. No cloud calls or remote writes occurred.
This closes requested-time reservation only; assessed runner/collector signing,
general dispatch, worker integration and measured usage reconciliation remain open.

## M3 — Oct 21–23: Verification and experience
- [x] Candidate/clean-replay report qualification core (2026-10-21): separate
  signature purpose, exact planned operation/tree/report binding, exit-zero
  regression presence and strict JUnit outcome checks. Pair validation requires
  identical immutable inputs and distinct baseline/candidate/replay operations.
  Tiny synthetic focused acceptance: **81 passed**, zero skips, exit 0
  (`m3-replay-receipts-focused.xml`). Full disposable-PostgreSQL regression:
  **940 passed**, zero skips, one upstream warning, 198.07s, exit 0
  (`m3-replay-receipts-postgres.xml`), tested from base `27c854e` plus this change.
  Ruff lint and strict mypy (87 files) pass; changed-file formatting/diff checks
  and credential-pattern scan pass. Repository-wide formatting/diff checks found
  a concurrent unrelated trailing-space edit in `services/runner/smoke.py:97`,
  left untouched. Initial negative-test data reused its original digest; corrected
  before this final full run. No dependency/UI changes, cloud calls or remote
  writes. This is authenticated protocol testing with ephemeral test keys,
  not deployed collector evidence. Durable candidate delivery, scheduling and
  live gate production remain pending; see `docs/replay-receipts.md`.
- [ ] Clean replay in independent fresh runner. Acceptance: image/source/test digests recorded.
- [~] Mandatory deterministic gate evaluator: authenticated core `f1b7acf` now requires seven architecture gates, signed run/source/patch/test/image/configuration context, intact manifest bytes and independent baseline/candidate/replay identities. Unsigned records return GATES_SATISFIED with no authority. Forgery/tamper/context/omission negatives passed within 830 tests (`m3-gates-postgres.xml`). Live producer deployment and live end-to-end acceptance remain pending; no live VERIFIED result is claimed. See `docs/verification-gates.md`.
- [~] Manifest hashing, artifact access control and retention: `d62d607` adds atomic private bundles, owner/lease/version/consent checks, integrity-checked restart reads, authenticated manifest/download endpoints and fixed 24-hour read expiry. Final 845 tests passed with zero skips (`m3-evidence-store-postgres.xml`); runtime mutation/deletion and unauthorized/expired/tampered downloads are denied. Physical purge, backup retention, encryption-at-rest deployment and full M3 integration remain pending. See `docs/evidence-storage.md`.
  Checkpoint-bound evaluation now reloads private artifact bytes under current
  owner, worker fence, version, stage and consent checks; expected context comes
  from the checkpoint plus trusted operator image/configuration. Thirteen small
  synthetic PostgreSQL cases passed (`m3-evidence-evaluation-focused.xml`), including
  forged signatures, changed patch/test identities and expired/tampered evidence.
  Standalone evaluation leaves checkpoint/events unchanged; durable acceptance is recorded below.
  Full regression: **858 passed, zero skips**, one upstream warning, 171.81s,
  exit 0 (`m3-evidence-evaluation-postgres.xml`); Ruff lint/format and strict mypy passed.
- [x] Durable authenticated verdict journal and private read API: migration 0017,
  signature/actual-byte revalidation inside the committing transaction, exact
  owner/lease/version/consent binding, hashed private verdict, atomic checkpoint/
  event/job completion and restart-safe exact duplicate reconciliation. Focused
  acceptance: **44 passed**, zero skips, exit 0 (m3-verdict-focused.xml), including
  31 journal/API scenarios and 13 existing evaluation cases. Runtime update/delete,
  forged/stale evidence, wrong owner and rollback/lease expiry are covered. No live
  producer or VERIFIED repair is claimed; see docs/verification-journal.md.
  Full disposable-PostgreSQL regression: **889 passed, zero skips**, one upstream
  warning, 192.93s, exit 0 (`m3-verdict-postgres.xml`); Ruff lint/format (161 files),
  strict mypy (82 files), scoped secret and diff checks passed. No cloud use.
- [x] On-demand research broker and citation timeline: migration 0018 persists
  sticky query intent, run-level search/time/declared-spend budgets, private
  hashed reports and RESEARCH_RETAINED events. Triage consumes citations only as
  untrusted context. One explicitly approved live lookup on 2026-10-21 returned
  two sources / one usage credit (m3-live-research.json). Archive hash and timeline
  rendering pass without another request. Provider ranking was not version-exact;
  the record is advisory and does not prove a diagnosis. See docs/m3-acceptance.md.
- [x] Local Next.js workbench: actual recent-run discovery, incident intake,
  persisted timeline, cancel/reload, candidate diff, gate matrix and SHA256-checked
  text artifact previews. Server-only API bearer and closed loopback BFF; no mock
  statuses ship in the app. The real API/database browser journey passed;
  rejected-candidate rendering uses explicitly synthetic contract evidence.
  Local-only scope; no public auth/deployment or live VERIFIED repair is claimed.
- [x] Browser finite-SSE replay reconnects with last validated ID, bounded backoff
  and deduplication; interrupted delivery preserves cursor. Error/missing/loading
  states, keyboard-native controls and mobile overflow were checked. Final browser
  harness: 7 passed, zero skips, 20.5s (m3-web-browser.xml); production build, UI
  lint/typecheck and npm audit (zero known vulnerabilities) passed.
**Gate:** judge can inspect a complete verified or rejected outcome in UI.

**M3 status (2026-10-21): NOT CLOSED.** Local UI/research/reconnect acceptance
passes, but required live seven-gate clean replay still lacks the trusted producer,
assessed runner configuration/image binding and correlated signed reports. The
existing fixed-fixture replay is not promoted to production VERIFIED. Physical
retention/deployment storage guarantees remain open. See docs/m3-acceptance.md.
Retained full backend evidence at 65bccad: 905 passed, zero skips, suite time
191.980s (m3-workbench-research-postgres.xml). It was not redundantly rerun for
this frontend-only/retained-evidence update; current Ruff/mypy and browser checks
passed. Exactly one approved Tavily lookup ran; no inference, sandbox execution,
remote repository write, merge or deployment occurred.

M3 local validation update (2026-10-20): Ruff lint/format (157 files), strict
mypy (81 files), targeted credential-value scans and Git diff checks passed.
The full disposable-PostgreSQL suite passed 845 tests in 164.43 seconds, exit 0,
with one upstream AnyIO warning. M2 fixture acceptance remains intact. No cloud
calls or remote writes occurred; production VERIFIED, UI and PR authority are
not implied by these primitives.

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
