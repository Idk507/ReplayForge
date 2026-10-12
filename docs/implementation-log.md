# Implementation Evidence Log

Documentation baseline created 2026-10-12. Local M0 inference and sandbox probes implemented;
live model calls, isolation tests, pull requests, deployments and benchmarks
remain unverified. Changes are uncommitted. Parent repository HEAD is
`25b9bdf7af6561f50603595cd0f5b13b9a124329`; it is not a build commit containing
this implementation. Runtime: uv-managed Python 3.12.12 on Windows.

## 2026-10-12 — M0 first independently testable unit

Implemented packages/providers/nebius.py, scripts/check_nebius.py, pinned
pyproject.toml/uv.lock, offline unit/boundary tests, environment example and
onboarding. Assessment: docs/implementation-assessment.md. Decision: ADR-008.

| Command | Observed exit/result |
|---|---|
| `uv sync` | 0; installed 22 packages including editable ReplayForge |
| `uv run pytest -q` before provider implementation | 1; missing provider module during collection (development RED, not an incident reproduction) |
| `uv run pytest -q` after implementation | 0; 20 passed |
| Initial `uv run ruff check .` | 1; seven long-line violations; fixed by formatter |
| Expanded pytest before short parameter IDs | Failed; 24 passed, 2 setup/teardown errors caused by oversized Windows test ID; corrected without removing the oversized-response case |
| `uv run pytest -q --junitxml=docs/evidence/m0-probe-tests.xml` | 0; 25 passed in 0.29s; no skips |
| `uv run ruff check .` final | 0; all checks passed |
| `uv run ruff format --check .` final | 0; six files already formatted |
| `uv run mypy` final | 0; no issues in five source files |
| `uv lock --check` | 0; 22 packages resolved |
| `uv run python -m scripts.check_nebius` | Tool-reported process exit 1; safe JSON `NOT_VERIFIED / CONFIG_MISSING_KEY`; no network |

JUnit artifact SHA256:
`f040af212c6e7f1a3b311bcdeeedb75a3f00c9e3e126bc2f4c668fffce6bcde8`.
The CLI entry function returns 2 for unavailable/not-run (covered in tests);
the execution wrapper reported 1 for the command above. Preserve that observed
distinction instead of claiming a successful live check.

Security review of this unit: exact HTTPS host/path restrictions, no redirects
or environment proxies, hidden SecretStr credential representation, sanitized
exceptions, bounded response size/timeouts, fixed synthetic prompt and explicit
network opt-in. Tested missing keys/models, disallowed destinations, provider
errors, malformed/truncated responses, oversized bodies and no-network default.
No untrusted repository code was executed. HTTPX MockTransport tests are offline
tests, not live integration evidence. No arbitrary endpoint bypass exists.

Dependency vulnerability scan, secret scanner, full contract suite, CI,
clean-clone, UI and E2E gates have not run and are not claimed passed. M0 remains
open; no tracker checkbox is marked complete. No OSS license is selected yet.

Next unit: runner Protocol/capability contracts and a remotely executed,
explicitly authorized smoke test with immutable image identity, denied-network
checks, timeouts and termination evidence. Beta Nebius Sandboxes is a candidate;
account access and compatibility have not been observed.

| Date | Milestone / test | Command or observed action | Exit / measured result | Commit / trace / artifact | Owner |
|---|---|---|---|---|---|
| Pending | M0 model smoke | Not run | Not verified | — | — |
| Pending | M0 runner smoke | Not run | Not verified | — | — |
| Pending | M2 baseline repair | Not run | Not verified | — | — |
| Pending | M4 approval PR | Not run | Not verified | — | — |

Always record failures, missing external service access and genuine observed metrics.

## 2026-10-12 — M0 sandbox probe and lifecycle boundary

Re-read the active milestone/specifications and inspected actual Python sources,
tests and Git state. The existing 25-test inference suite passed before editing.
Parent HEAD remains `25b9bdf7af6561f50603595cd0f5b13b9a124329`; ReplayForge is
still untracked in the parent repository. No build commit contains these local
changes. No parent branch/history, GitHub workflow or remote was modified.

Implemented:

- Shared versioned Pydantic sandbox probe/report contracts.
- Narrow SandboxBackend Protocol and a real documented Nebius REST client.
- One fixed literal-argv guest probe; empty env, non-root UID/GID, disabled
  networking, disposable filesystem, guest timeout/output/writable-layer caps.
- Correlated operation/image checks, strict request metadata and process/output
  validation; explicit unknown submissions with no automatic POST retry.
- Bounded cancellation and terminal-state confirmation for known owned work.
- Safe reports with evidence-origin labels and recomputable embedded-observation
  SHA256. No raw provider text or credential-bearing logs are retained.
- Offline-by-default CLI; wheel packaging now includes services and scripts.
- ADR-009, sandbox capability/admission guide and safe environment examples.

Actual development results:

| Command/check | Observed result |
|---|---|
| `uv run pytest -q` before edits | 0; existing 25 tests passed |
| `uv run pytest -q tests/contract/test_sandbox_smoke.py` before code | 1; missing packages.contracts during collection (development RED only) |
| Same contract command after implementation | 0; initial 25 contract tests passed |
| `uv run pytest -q tests/unit/test_sandbox_lifecycle.py` before cancellation fix | 1; 1 failed, 9 passed; cancellation conflict prevented a terminal read |
| Final `uv run pytest -q --junitxml=docs/evidence/m0-sandbox-tests.xml` | 0; 66 passed in 0.50s, no skips (25 existing + 41 new) |
| Final `uv run ruff check .` | 0; all checks passed |
| Final `uv run ruff format --check .` | 0; 17 files already formatted |
| Final `uv run mypy` | 0; no issues in 14 source files, including services |
| `uv lock --check` | 0; 22 packages resolved; no dependency additions |
| `uv build --wheel` | 0; replayforge-0.1.0-py3-none-any.whl built |
| Isolated `uv run python -I` wheel-import check | 0; contracts, remote runner and CLI imported from the built wheel |
| `uv run python -m scripts.check_sandbox` | Tool-reported exit 1; `NOT_VERIFIED / S_CONFIG_MISSING`; no network call |

The CLI entry function's nonzero result 2 is asserted in unit tests; the shell
wrapper reported 1 above. Raw observed final command results are stored in
`docs/evidence/m0-sandbox-checks.json`.

Artifact SHA256 values:

- JUnit: `40f18926d854f81d8a3f50d05f6861838d25a1bd1ad3c437242d768312708cca`
- Check ledger: `ca0a38a6739926d5bb851c6982ce2a8724786194c2cbbda8bd75ee93bfb60381`
- Wheel (ignored local dist output): `216d9bc953d876b3706f805b393d708f93bd20f6a786a9f7b0b7aece15cc7ff9`

Threat review: no caller-supplied command/argv, arbitrary destination, guest
credentials or upload paths are exposed. Tests reject root/network/socket/env
violations, image/operation mismatch, saved state, missing evidence, substituted
boolean types, injected files, malformed/truncated/oversized output, auth failures
and redirects. Unknown POST outcomes are never retried. Known work is cancelled
after polling failure; cancellation conflicts still trigger a terminal read.
Cancellation acknowledgement alone never qualifies as cleanup. Poll count and
wall deadlines are bounded; no test runs guest code on this host.

All provider interactions in tests use explicitly fake offline HTTP transports.
Reports label these TEST_TRANSPORT; they are not live evidence. The implemented
REST path has not been executed against Nebius. Account activation, an approved
image UUID, required process configuration and explicit charge authorization
are genuine remaining M0 prerequisites. The current offline CLI observes at
least one missing required setting; no credential values were inspected/output.

Production arbitrary-repository execution remains disabled. Hard CPU/memory/PID,
read-only root, host-mount isolation, OCI image digest, retention/deletion and
signed durable artifact guarantees remain unverified. M0 cannot be marked
complete and M1 broad implementation has not started. Dependency vulnerability
and secret scanners, CI, clean-clone, UI/E2E and approval gates were not run.

Next task: supply/activate the sandbox project and approved prebuilt image,
authorize one synthetic remote smoke operation and obtain actual terminal and
network/isolation evidence. Separately authorize/configure live Nemotron smoke.
Resolve the production capability gaps with provider evidence or a designated
remote Linux runner before enabling repository execution. No paid call, remote
write, deployment or PR was performed in this session.

## 2026-10-12 — Optional Tavily M0 adapter

Inspected milestone documents, current code and prior evidence. Earliest live
critical-path acceptance still needs authorized Nebius inference and isolated
remote execution. Implemented the independently testable M0 Tavily access
adapter while those prerequisites remain unresolved; broad M1 work stays gated.

Added typed curated documentation queries, fixed-host bounded HTTP search,
source URL admission, normalization/redaction, canonical sanitized content
hashes, explicit untrusted provenance, safe errors and an offline-first CLI.
No incident/repository/free-form input is accepted. Added 44 offline tests for
poisoned snippets, credential handling, URL attacks, malformed/oversized results,
timeouts, status errors, concurrent budget exhaustion and lifecycle admission.
Official API reference: https://docs.tavily.com/documentation/api-reference/endpoint/search .

Development red phase: research contract test collection initially failed
because the module did not exist. Initial lint and typing errors were fixed.
These development failures are not incident reproduction evidence.

Final validation: 110 tests passed in 0.93s; ruff check, ruff format --check
(24 files), mypy (19 source files), uv lock --check (22 packages), wheel build
and isolated wheel-origin import assertions all exited 0. Offline smoke returned
NOT_RUN / DENY_DISABLED; its function exit 2 is tested, while this PowerShell
execution wrapper reported exit 1. Commands and hashes are recorded in
`docs/evidence/m0-tavily-checks.json`; JUnit is `m0-tavily-tests.xml`.
Parent HEAD remains 25b9bdf7af6561f50603595cd0f5b13b9a124329; it is not a
commit containing these local uncommitted changes.

Threat review: returned text cannot authorize tools; registered domains and
closed query vocabulary limit disclosure and SSRF. Redaction is conservative,
not comprehensive DLP. The two-call client cap is not a durable run budget;
broker policy/spend accounting, artifact storage and authentic agent timeline
integration remain future work. Missing usage stays unknown. All HTTP tests
are labeled TEST_TRANSPORT and perform no live requests.

M0 remains partial. Next critical task is approved live Nemotron and remote
sandbox probes with configured account/project/approved image, followed by
authorized Tavily access verification. M3 research acceptance remains open.
No paid call, remote write, deployment or PR was performed. Dependency and
secret scans, CI, clean-clone, UI/E2E and approval gates remain unverified.

## 2026-10-12 — Critical-path reassessment

Reread AGENTS, architecture, playbook, milestones, tracker and active M0
limitations; inspected implemented CLIs, packaging and prior test evidence.
No repository-scoped committed changes supersede the local implementation.
Revalidation: `uv run pytest -q` exited 0 (110 passed in 0.48s),
`uv run ruff check .`, `uv run ruff format --check .` (24 files),
`uv run mypy` (19 source files) and `uv lock --check` all exited 0.
No new implementation or completed acceptance box is claimed by this review.

The earliest remaining critical-path acceptance requires real NVIDIA inference,
then remote isolation evidence. Explicit authorization for possible provider
charges has not been supplied. Broad M1 implementation remains stopped by the
M0 gate in todo.md; offline adapters cannot substitute for this evidence.
No paid request, remote write or local untrusted-code execution was performed.
Next action requires operator configuration and explicit live-probe approval.

## 2026-10-12 — Local M1 foundation authorized while M0 is pending

The user's explicit continuation authorizes independent local foundation work
while sandbox activation is pending (ADR-011). Reread governing documents,
contracts and existing implementation. Added immutable RunState identity,
bounded SHA256 artifact references, architecture stage enums and a pure reducer
with stale-version, cancellation, terminal-state and illegal-transition checks.
VERIFIED and PR publication transitions fail closed pending trusted services.
Added bounded remaining action counters and pure admission/debit logic.

Both slices began with failing test collection for absent modules (exit 1),
then implementation passed. Final commands: `uv run pytest -q
--junitxml=docs/evidence/m1-foundation-tests.xml` exited 0, 147 passed in 0.54s
(37 new tests); `uv run ruff check .`, `uv run ruff format --check .` (30 files),
`uv run mypy` (23 source files), `uv lock --check` (22 packages) and
`uv build --wheel` each exited 0. JUnit SHA256:
dee811359294820e93f9e4b2a80468856a87eabe9d0585d2cfd1668561a11bfb.
Parent HEAD remains 25b9bdf7af6561f50603595cd0f5b13b9a124329; changes are
local and uncommitted, not a merged implementation commit.

Threat review: reducer revalidates model_copy/model_construct bypasses and
excludes arbitrary raw checkpoint fields. Counters cannot become negative or
consume booleans. Pure functions are not durable concurrency control or an
authorization boundary. Evidence references alone do not prove authenticity.
Run-level persistence/events, spend/time reservations and full agent contracts
remain incomplete. M0 and M1 acceptance remain open. No paid call, remote write
or untrusted host execution occurred. Next task: PostgreSQL schema and atomic
checkpoint/job recovery integration; obtain a local test database before claiming
crash/restart acceptance.

## 2026-10-12 — Real local PostgreSQL and API slices

Reread governing documents, active contracts and recent local implementation.
The user confirmed PostgreSQL installation and authorized continuation; a prior
read-only query identified PostgreSQL 18.6. The user selected local-only API
authentication during this implementation. No cloud/write approval was inferred.

Added pinned Psycopg, SQLAlchemy and Alembic, migration 0001 for checkpoints,
events and job leases, and a trusted Store adapter. Creation is idempotent and
atomic; owner-scoped reads conceal other owners. Checkpoint version checks,
lease tokens, heartbeats, expiry and bounded claim attempts fence stale workers.
Budget reservation persists before dispatch; exhausted jobs have an explicit
status and audit event. Events contain only stage/version/trace metadata.

Added an authenticated loopback-only FastAPI factory for metadata intake, reads,
cancellation and finite SSE replay. Input bytes are bounded and schema errors
do not echo incident bodies. Server-side repository UUID grants fail closed.
Incident input is hashed and discarded until redacted artifact storage exists.
No worker, repository fetch, model/sandbox dispatch, verifier or PR API is enabled.

Tests first failed because modules were absent. Real concurrency tests then
exposed duplicate claims with run-only locking; locking both run/job rows fixed
that race. Concurrent idempotent inserts also exposed contention resolved by a
transaction-scoped owner/key advisory lock before insertion. A misplaced test
assertion was corrected. No failing test was disabled to obtain acceptance.

The initial Starlette 0.48.0 audit reported six advisories. Updated the new API
pins to FastAPI 0.143.0 and Starlette 1.3.1; final production graph pip-audit
reported no known vulnerabilities. The raw final audit is preserved. Secret scan
found eight entries, all inspected: synthetic keys, rejection URLs and a field
label in existing negative tests; no live secret found in scanned source scope.
No exclusions were added to conceal findings. Scan coverage is not a guarantee
that secrets cannot exist elsewhere (including chat or local service profiles).

Final validation: `uv run python -m scripts.test_postgres` exited 0,
169 passed in 8.50s. The harness prompted without echo, created disposable local
database/migrator/runtime roles, and cleaned them after execution. Tests cover
real process-death rollback, restart/reclaim, simultaneous claims, idempotency,
atomic failure rollback, budget concurrency, append-only runtime permissions,
migration downgrade/reapply, authenticated API intake/cancel and SSE cursor replay.
Two third-party test-client deprecation warnings remain (HTTPX compatibility and
AnyIO BlockingPortal alias); they were not suppressed. Ruff lint/format (39 files),
strict mypy (28 source files), lock check (36 packages) and wheel build exited 0.
Commands, artifact hashes and scan outcomes are in
`docs/evidence/m1-postgres-checks.json`; JUnit is `m1-postgres-tests.xml`.
Parent HEAD is still 25b9bdf7af6561f50603595cd0f5b13b9a124329, not a commit
containing these local changes. No permanent dev database or credentials created.

Threat review/limits: runtime cannot perform DDL or rewrite/delete events, but
the adapter is trusted application code, not database tenant RLS. Schema auth,
repo/incident provenance tables and full identity constraints are incomplete.
Lease fencing protects commits, not exactly-once external side effects. Exhausted
jobs need explicit reconciliation; automatic terminal routing is pending.
Cost/time budget enforcement, OTel, full domain tables, continuous SSE, production
OIDC and rate limits remain incomplete. M0 and M1 stay open; local tracker entries
are partial, not merged issue completion. UI/E2E and end-to-end verified-repair
acceptance remain unrun. See docs/postgres-local.md and ADR-012.

Next critical local task: consented repository registry/read-only pinned snapshot
and redacted incident/context artifacts, then graph wiring. Actual fetch acceptance
needs an explicitly approved repository/commit and GitHub installation/scopes;
none are configured by this slice. Live sandbox activation/isolation and inference
remain separate external acceptance boundaries. No paid call or remote write ran.
