# ReplayForge — Delivery Tracker

**Planning date:** Monday 12 Oct 2026 | **Hard deadline:** Fri 30 Oct 2026, 10:00 PDT / 22:30 IST. All boxes initially unchecked: no implementation or live test is asserted by this documentation package.

## Current focus - reconciled 2026-10-24 collection handoff

Implementation 3f5704f adds the authenticated collection broker, isolated worker
launcher and selected-file acquisition under the original registered grant.
Full backend regression:1335passed,zero failures/errors/skips,499.37s,exit0.
This full run predates the dependency-only pytest/certifi updates; post-update
unit/contract922passed6.54s and focused PostgreSQL evidence are recorded in
collection-worker-checks.json. Ruff/format/mypy/lock and dependency audit results
are recorded there. See docs/authenticated-collection.md for safe startup.

- [x] Authenticated intake-to-collection-to-TRIAGING handoff, including current
membership/consent and lease/version fences. Live small-file read retained one
context file/source manifest at exact pinned SHA; no inference/sandbox/write.
README metadata-only and missing-file INCONCLUSIVE results are preserved.

M2 fixed-checkout contingency remains accepted. M3 authentic seven-gate replay
and integrated repair worker, M4 session-bound approval/live PR/hidden evaluation,
and M5 deployment/submission remain open. No milestone checkbox is closed by
collection acceptance. Repository selection currently pins the default-branch
SHA; explicit branch/commit selection remains follow-up work.

Next: incident-driven bounded relevant-file selection and triage/repair worker
handoff on a small real incident, then authentic runner/verifier acceptance.
The live acceptance incident explicitly requested collection only; no repair
or VERIFIED claim may be inferred from reaching TRIAGING. Approval/publication
requires exact independently authenticated human authority and explicit writes.

## Historical focus snapshot � reconciled 2026-10-23

Implementation `0042c9d` adds the bounded GitHub publisher, exact Git text/tree/
commit construction, approval-bound controller and immutable result recovery.
`7c88d10` corrects the test's expected sanitized PostgreSQL permission denial.
Latest full local evidence: `docs/evidence/m4-publisher-final-postgres.xml`,
**1234 passed**, zero failures/errors/skips, 446.58s, exit 0. Ruff and formatting
(224 files), strict mypy (111 sources), scoped credential-pattern and diff checks
pass. `docs/evidence/m4-publication-checks.json` records the exact JUnit checksum.

M2 remains accepted within the fixed-checkout contingency. M3 remains open for
authentic seven-gate production, admitted baseline/materialization and the full
worker/UI journey. M4 now has a locally tested actual REST publisher and durable
PR result journal, beyond its approval/intent policy. Live approved publication
and hidden evaluation remain open; M4 is not complete. M5 packaging/deployment/
submission remain open. Authentication was reassigned to this workstream on 2026-10-23; see tasks/plan.md.

The publisher loads the exact reviewed diff from retained evidence, reloads
trusted human permission/approval/current consent before every POST, checks actual
Git object hashes, creates only a new deterministic branch and draft PR, and
recovers matching remote objects after uncertain responses. It never executes
repository code on the host, force-pushes, merges or changes the target branch.
Results/event/checkpoint commit atomically; cancellation is never reopened.
Test transport cannot become real PR evidence. Test ports and all human/gate
identities in this regression are synthetic. No external write or paid call ran.
See `docs/github-publication.md` and `docs/implementation-log.md`.

Next critical task: authentic deterministic seven-gate production and admitted
small-fixture worker integration. Production signer/OCI/configuration assessment
and teammate-owned human identity integration remain prerequisites for live
VERIFIED acceptance. Then obtain exact current approval and explicit write
authorization for one tiny draft PR, verify retry discovery, and run meaningful
hidden evaluation. Preserve the original milestone exits; local contracts alone
cannot close them. No public approval route or automatic writer was enabled.

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
- [x] Prepared-image Nebius REST backend, offline acceptance (2026-10-22):
  exact prepared image/OCI/plan/execution/tree bindings, capability checks,
  network-disabled one-use POST, owned status/cancellation, explicit live opt-in
  and TEST_TRANSPORT provenance. Real adapter/controller composition prevents
  duplicate HTTP on restart. Source was concurrently checkpointed in `d56714e`.
  Contract: **21 passed** (`m3-prepared-backend-contract.xml`); initial focused
  database composition: **1 passed** (`m3-prepared-dispatch-focused.xml`). Final
  full database regression: **1026 passed**, zero failures/errors/skips, 289.65s,
  one upstream warning, exit 0 (`m3-prepared-backend-postgres.xml`), including the
  successful and timed-out HTTP/restart paths. Global Ruff lint/format (186 files),
  strict mypy (93 files), diff and scoped credential-pattern checks passed.
  Formatted the now-committed smoke.py whitespace without behavior change.
  Initial full run: 1025 passed and one existing crash-test child-process timeout;
  unchanged isolated and final full runs passed. Failed report preserved in
  `m3-prepared-backend-first-run.xml.gz`; no deadline/assertion was weakened.
  The transient cause was not reproduced. No live API/cloud calls or remote
  writes; authentication unchanged. Materialization and JUnit export/retention
  remain open: the disposable command cannot expose its guest JUnit file after
  completion. No live VERIFIED/M3 closure claimed. See `docs/prepared-nebius-backend.md`.
- [x] One-shot admitted replay dispatch controller, contract acceptance
  (2026-10-22): migration 0020 retains per-phase submission claims before backend
  I/O and globally unique provider operation bindings afterward. Current scope,
  owner/lease/version/cancellation, exact reserved plans, signed capabilities and
  backend identity are checked; live ports additionally require explicit
  authorization. Concurrent/restarted calls cannot repeat submission; uncertain
  outcomes stay sticky. Added bounded audit event vocabulary after the initial
  focused tests exposed its missing database allowlist entries. Retained focused
  evidence: **21 passed**, zero skips (`m3-replay-dispatch-focused.xml`, completed
  before the interruption). Final current-tree regression: **1009 passed**, zero
  failures/errors/skips, 315.38s, one upstream warning, exit 0
  (`m3-replay-dispatch-postgres.xml`), base `399a424` plus this change. Ruff lint,
  strict mypy (92 files), changed-file formatting and scoped credential-pattern
  scans passed. The unrelated smoke.py whitespace edit is preserved. All backend
  calls used a labelled fake port and ephemeral assessment keys; no cloud request
  or remote write occurred. No production prepared-workspace backend is installed;
  real materialization, polling/cleanup, collector configuration and live M3 gates
  remain pending. See `docs/replay-dispatch.md`.
- [x] Immutable replay-pair intent and atomic accounting (2026-10-21): migration
  0019 and ReplayJournal reserve exact candidate/clean plans and distinct action
  identities against current checkpoint/baseline/consent/fence. Both commands and
  full 120-second requested time commit once with the intent and budget event;
  exhaustion closes INCONCLUSIVE without partial debit. Delivery now requires
  the exact retained reservation. Runtime update/delete are denied. Fixed an
  observed duplicate-delivery race without weakening transactional fencing; a
  deterministic interleaving test covers it. Focused PostgreSQL: **28 passed**,
  zero skips, exit 0 (`m3-replay-reservations-focused.xml`). Full regression:
  **988 passed**, zero failures/errors/skips, 243.39s, one upstream warning,
  exit 0 (`m3-replay-reservations-postgres.xml`), base `76840a2` plus this change.
  Ruff lint, strict mypy (91 files), changed-file format/diff and scoped
  credential-pattern checks passed. The unrelated smoke.py trailing-space edit
  remains untouched. No cloud calls or remote writes. This reserves intent only;
  assessed admission, actual submission/polling, collector and live seven-gate
  acceptance remain open. See `docs/replay-reservations.md`.
- [x] Durable candidate/replay receipt delivery (2026-10-21): internal worker
  receiver binds current source/repository/patch/test and accepted baseline,
  authenticates both reports using database time, and atomically retains exact
  plans/receipts/JUnit bytes in the private evidence bundle. Duplicate restart
  and concurrent delivery commit once; conflicting evidence, stale workers,
  cancellation, revoked consent and audit rollback are rejected. Authentic
  failing results remain evidence and never manufacture VERIFIED. Focused real
  PostgreSQL acceptance: **13 passed**, zero skips, exit 0
  (`m3-replay-delivery-focused.xml`). See `docs/replay-delivery.md`.
  Final full regression from base `3315bca` plus this increment: **973 passed**,
  zero failures/errors/skips, 217.31s, one upstream AnyIO warning, exit 0
  (`m3-delivery-m4-approval-postgres.xml`). Ruff lint, strict mypy (90 source
  files), changed-file formatting and credential-pattern checks passed. Global
  formatting still has the preserved unrelated trailing space in smoke.py:97.
  No dependencies/UI changed; no cloud requests or remote writes occurred.
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

M3 implementation evidence (2026-10-22, base f5f1c54): explicit saved-image
JUnit export now passes owned-image/status/report-size/retention-policy negatives.
The default disposable template is preserved; saving filesystem state changes the
canonical plan hash. Full local PostgreSQL regression: **1041 passed**, zero skips,
300.91s, exit 0 (`m3-junit-export-postgres.xml`); focused contracts: 69 passed.
Ruff lint/format and strict mypy pass. No live execution or VERIFIED claim.
Trusted producer, materialization/orchestration and assessed live seven-gate
acceptance remain pending; M3 stays open. Whole-filesystem retention and lack of
physical deletion guarantees are documented in docs/prepared-nebius-backend.md.

- [x] Repository-scoped advisory outcome memory (2026-10-22, base f1a25d5):
  durable accepted records recall across restart with owner/repository/policy/
  consent/expiry/integrity bounds; triage includes cards inside the existing
  hashed request/context budget and completed recovery makes no duplicate call.
  Full disposable-PostgreSQL suite **1057 passed**, zero skips, 303.33s, exit 0
  (`m3-memory-postgres.xml`); Ruff lint/format and strict mypy pass. Existing
  per-run recovery is preserved. This is structured historical outcome recall,
  not semantic learning, longer retention, live accuracy or M3 completion.
  See docs/memory.md; the source-informed harness follow-up is recorded below.

- [x] Source-informed bounded collection/triage harness (2026-10-22, base
  d5cf18e): combined fair scheduling, cooperative shutdown, finite steps/waits,
  durable restart/uncertain-request handling and CLI inference/cost opt-in.
  Small real-PostgreSQL/mock-provider handoff and failure/restart cases pass;
  full regression **1074 passed**, zero skips, 333.06s, exit 0
  (`m3-harness-postgres.xml`). Ruff lint/format and strict mypy pass. The runnable
  portion reaches REPRODUCING; admitted execution, signed report production and
  live seven-gate acceptance remain pending. M3 remains open. Source rationale,
  commands and boundaries: docs/agent-harness.md.

- [x] Durable known-replay observation collection (`c949e1e`, 2026-10-22):
  bounded GET-only report/process collection, exact plan/operation correlation,
  live lease/version/consent fencing, private atomic retention/audit, checksum/
  expiry checks and restart without HTTP. Pending/terminal race and concurrent
  terminal delivery reconcile; audit rollback and immutable runtime role pass.
  Full local PostgreSQL regression **1104 passed**, zero skips, 373.37s, exit 0
  (`m3-replay-collection-postgres.xml`); Ruff lint/format and strict mypy pass.
  Observations remain unsigned and cannot authorize verification. M3 stays open
  for independent workspace/image assessment, trusted receipt production and
  live seven-gate acceptance. See docs/replay-collection.md.

## M4 — Oct 24–26: External write and benchmark
- [~] M4 started (2026-10-21): deterministic human approval binding policy
  validates exact run/repository/owner/source/diff/verdict/branch/policy,
  independently supplied reviewer permission, explicit approval, TTL and nonce
  availability. **20 unit cases passed**, zero skips, exit 0
  (`m4-approval-policy.xml`), included in the 973-test full run above. This
  validation kernel has no publication authority. Durable approval journal,
  independently authenticated human endpoint and atomic nonce consumption are
  still required before the approval task below can close. See
  `docs/approval-policy.md`. No GitHub action was performed.
- [ ] Approval bound to immutable patch digest and reviewer authorization. Acceptance: changed patch invalidates approval.
- [~] One-use GitHub PR publisher: actual REST adapter/controller and durable
  result recovery pass local contracts at 0042c9d/7c88d10 (1234 full backend cases).
  Live acceptance remains pending: retry creates exactly one real approved PR.
  See docs/github-publication.md; no external write was performed.
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

## Final acceptance ledger template (not current status)
| Gate | Expected evidence | Observed result | Commit / artifact | Date |
|---|---|---|---|---|
| Live NVIDIA on Nebius | request ID + model + timestamp | Pending | — | — |
| Sandbox isolated | policy negative test + runner log | Pending | — | — |
| Baseline reproduced | original failure signature | Pending | — | — |
| Patched clean replay | regression and existing suite | Pending | — | — |
| Approval-gated PR | exact digest + PR URL | Pending | — | — |
| Deployment accessible | public health/demo screenshot | Pending | — | — |
| Devpost submission | public submission URL | Pending | — | — |

## GitHub authentication workstream — planned 2026-10-23

User has reassigned authentication implementation to this workstream. The earlier
teammate ownership statements describe the prior arrangement; the new plan is
`tasks/plan.md` with proposed ADR `docs/adr/014-github-authentication.md`.
No authentication code or live login is claimed by this planning increment.
- [x] A: explicit auth settings and bounded mocked GitHub transport (51e4343,
  43 focused/auth and 1277 full backend cases; auth-foundation-checks.json).
- [x] B: browser-bound one-use OAuth/PKCE and durable opaque sessions
  (51e4343, same full local evidence; provider/human identities synthetic).
- [x] C: session principal/CSRF and owner-filtered private API/SSE/artifact
  composition, preserving local mode (638feb6). Full local backend: 1299 pass,
  zero skips, auth-session-final-postgres.xml; see auth-session-checks.json.
- [~] D: current user/installation repository discovery, exact-commit consent,
  UUID mapping and owner disconnect implemented; real GitHub acceptance pending.
- [ ] E: authenticated exact review and scoped installation write-token broker.
- [~] F: login/logout/session/repository UI implemented; auth browser3 pass,
  local browser6 pass/1 opt-in real-API skip. Lint/types/build pass. Actual human
  GitHub login and separated-role startup acceptance remain pending.
C/D local contracts are accepted within synthetic-provider scope. No real
credentials, paid calls or external writes were used. Next: session-derived exact
review and reduced installation-token brokerage, then configured human login.
Authentication completion is separate from seven-gate/M4 evaluation acceptance.


Latest local continuation (2026-10-23): 638feb6 adds session/repository API and
Next.js integration. Full backend1299 pass, zero skips,417.46s. Earlier current-
focus statements about pending session integration/teammate ownership are
superseded. M3/M4/M5 exits remain open; see auth-session-checks.json.


2026-10-23 local auth setup: independent keys and a new dedicated PostgreSQL
database/three restricted roles prepared in ignored .env. Migration0026 and actual
separated-role startup plus anonymous /auth/me and /runs401 passed with temporary
synthetic App configuration; no OAuth call. First disposable attempt used a wrong
migration URL format and was cleaned; corrected URL passed. Existing databases
untouched. See docs/evidence/auth-local-setup-checks.json and docs/github-app-setup.md.
Remaining external step: user registers/installs the App and privately configures
Client ID/secret, then completes browser consent. Actual login remains unverified.


2026-10-23 live GitHub login smoke: user supplied App client settings privately.
Validated configuration and restricted-role startup; enabled local GitHub mode.
Live callback initially rejected standard OAuth iss parameter. RED regression
proved valid exact issuer was rejected; allowlisted iss with exact provider check
before journal consume/code exchange. Wrong issuer/suffix remain denied; legacy
no-issuer callback remains supported. RFC9207 section2.4 consulted (rfc-editor.org).
Focused auth_app/session_access regression21pass,1281deselected,21.71s,exit0;
Ruff/format240files and strict mypy119sources pass. No full1299 rerun for narrow fix.
Fresh browser login visibly showed Idk507; user had already authorized the App.
Integrations read returned no visible installations. Signed out; browser login
screen and database revocation of all created user sessions confirmed. No repository
connected/executed, no PR/write, no inference/sandbox/research charge. Provider token
exchange/user/installations reads occurred. Callback queries/secrets not retained
in evidence. Sanitized screenshots/report in docs/evidence/auth-live-*. Live
repository acceptance remains blocked on App installation/current visible access.
User must install replayforge-ai on only maf_ds_agent, then retry login/discovery.
Windows uv npm launch requires cmd /c; corrected setup guide. Development Next
server generated transient rules/type paths; these are excluded from this change.

2026-10-24 onboarding acceptance: [x] bounded large-account page contract and
explicit refresh/error recovery; [x] automatic default source pin and prefilled
intake handoff, observed in synthetic browser tests. [ ] updated actual user-tab
selector acceptance (blocked browser automation); [ ] full M3/M4 exits.


2026-10-24 user feedback follow-up (a6250b2): repository selection now uses
searchable radio rows, page-specific retry, and fresh consent per source. Local
auth browser4 pass; workbench7 pass/1 opt-in skip; build/lint/types pass. See
auth-picker-checks.json. Actual workbench sample is CREATED/queued,zero worker
attempts. Next priority: authenticated source-broker-to-worker handoff, then full
M3 runner/verifier progression; reviewer/write brokerage follows verified evidence.
Page2 provider metadata returned50 valid entries; intermittent UI failure is not
claimed reproduced. Actual user picker confirmation remains pending. M3/M4 open.
