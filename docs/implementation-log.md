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

## 2026-10-13 — Repository scope admission

Reread required docs and active contracts. Repository now has its own Git root
and tracked implementation; inspected recent commits and status. Current source
HEAD: e60e682128a1a7d10d1f5dc028ca9abb0adb76c7. New changes in this session
are uncommitted. Existing untracked .env.example was preserved.

Added RepositoryGrant and ScopeDecision contracts, exact caller/repository/commit
consent checks, canonical GitHub HTTPS URL admission and a fixed path vocabulary.
Added lexical Git-tree mode/path admission rejecting links, submodules, traversal,
host spoofing, credential URLs, Windows device paths and selected secret files.
These are pure policies, not a fetcher or persisted grant registry; grants must
come from trusted application configuration. No new authentication flow added.

Test-first collection initially exited 1 because repository contracts were absent.
New policy suite then passed 32 tests. Final real PostgreSQL harness command
`uv run python -m scripts.test_postgres` exited 0: 201 passed in 11.14s,
including the prior 14 database integration scenarios; two known third-party
test-client deprecation warnings remain. Saved new JUnit separately as
docs/evidence/m1-repository-tests.xml and restored the prior committed PostgreSQL
artifact after the harness overwrote its default filename, preserving the old
ledger hash. New JUnit SHA256:
821f00610f2a77d311720d18d0cc1b6b3d78951224de942e0407903e15663f4b.
Ruff check, format check (42 files), strict mypy (30 source files), lock check
(36 packages) and wheel build all exited 0. No dependencies changed; the prior
dependency/secret scans are historical and were not rerun for this slice.

Threat limits: lexical admission does not resolve filesystem symlinks/junctions
or detect case collisions. Caller authentication, immutable tree/blob verification,
safe bounded reading, persistent consent and registry/API integration are still
required. See docs/repository-admission.md. No repository snapshot acceptance
is claimed; M0 and M1 remain partial. User supplied provider activation and a
successful offline config report; no live execution/charge approval was inferred.
No cloud calls, remote writes or untrusted-code execution occurred.

Next: persistent repository registry and safe pinned snapshot reading, with an
explicitly approved repository/commit and installation scope for live fetch
acceptance. Live sandbox/inference proof remains an authorization boundary.

## 2026-10-13 — Object reader and persisted repository grants

Reread governing documents and actual implementation. HEAD remains
e60e682128a1a7d10d1f5dc028ca9abb0adb76c7; prior scope work and unrelated
untracked .env.example preserved. New changes are local and uncommitted.

Added bounded raw Git object reading with typed SHA-1 identity checks from
admitted commit through relevant trees to approved text blobs. Snapshot contains
read-only bytes and SHA256 file identities. Shared existing lexical predicate
for ancestor validation. No Git subprocess, checkout or host-path reads occur.
Added negative cases for malformed/tampered objects, scope denial, case collisions,
links/submodules, binary/LFS, missing/oversized objects and depth/read budgets.
Object transports are synthetic and do not establish live integration.

Migration 0002 and Store persist operator-owned grants. Runtime has SELECT only;
API intake checks persisted caller/commit consent. Real PostgreSQL tests cover
reopening, owner isolation, runtime INSERT denial and unapproved-commit HTTP 403.
Downgrade/reapply remains tested in a disposable database with separate roles.

Initial snapshot test collection exited 1 for an absent module. Final command
`uv run python -m scripts.test_postgres` exited 0: 221 passed in 10.90s,
20 new tests beyond 201; two known third-party test warnings remain. Ruff check,
format check (46 files), mypy (32 source files), lock check (36 packages) and
wheel build exited 0. Targeted new-source secret scan found zero findings;
dependencies unchanged, previous vulnerability audit remains historical.
Evidence: docs/evidence/m1-snapshot-checks.json and m1-snapshot-tests.xml.
Restored committed m1-postgres-tests.xml after saving the new harness output
separately, preserving historical ledger hashes.

Threat limits: SHA-1 identity is not author authenticity. Transport must enforce
timeouts/bytes before allocation. Text still needs redaction before model use.
Production ObjectSource, authenticated GitHub fetch, durable snapshots/artifacts,
installation-scope verification and grant revocation remain pending. Registry
methods are trusted internal code, not independent authentication. M0/M1 remain
partial; no live snapshot or verified-repair acceptance is claimed.

Next integration needs an explicitly approved repository/commit and GitHub
installation scope for real read-only fetch; none is configured in this session.
Redacted context/artifact storage follows. Live sandbox still requires explicit
charge authorization. No paid call, remote write or untrusted execution occurred.

## 2026-10-13 — Sanitized context construction

Reread required docs and actual implementation. Preserved existing work; source
HEAD remains e60e682128a1a7d10d1f5dc028ca9abb0adb76c7. Continued the
independently testable collector while live GitHub/sandbox acceptance is pending.

Added frozen ContextBundle/ContextFile, selected-path collection, original-source
integrity checks and separate original/sanitized hashes. Bounded normalization,
known/common credential redaction and AST symbol inspection produce an explicitly
untrusted context capped at 4096 serialized UTF-8 bytes. Oversized files are
omitted with reasons, not silently truncated. No source execution or model call.

Initial test collection exited 1 for the absent module. A later negative test
observed a truncated-private-key leak; the fix now removes an unterminated block
through end of input. Fifteen new tests cover sentinel exclusion from a labeled
model sink, credentials, metadata/control normalization, invalid encoding/syntax,
tampered hashes, input/context limits and inert injected instructions.

Final `uv run python -m scripts.test_postgres` exited 0: 236 passed in 11.87s,
including the 15 real database scenarios. Two known third-party warnings remain.
Ruff lint/format (50 files), mypy (35 source files), lock check (36 packages)
and wheel build exited 0. New-source secret scan found zero findings. Dependencies
unchanged; prior vulnerability audit is historical. New evidence is preserved in
m1-context-tests.xml and m1-context-checks.json; committed PostgreSQL JUnit restored
after saving the harness output separately, preserving historical ledger hashes.

Limits: pattern redaction is not comprehensive DLP; trusted callers must supply
known secrets. AST is sanitized context, not original executable semantics.
Durable context artifacts, dependency digest, automatic discovery, graph/model
gateway integration and real-fetch acceptance remain pending. Original raw
snapshot memory must stay private and unlogged. See docs/context-collection.md.
M0/M1 remain partial; no verified repair or live integration completion claimed.

Next: durable authorized context/artifact storage and graph integration. Live
fetch requires an approved repo/commit and installation scope; sandbox/inference
require explicit charge approval. No cloud call, remote write or untrusted
execution occurred. New changes remain local and uncommitted.

## 2026-10-13 — Durable sanitized context artifacts

Reread docs, contracts and actual changes; HEAD remains
e60e682128a1a7d10d1f5dc028ca9abb0adb76c7. Preserved prior work and unrelated
untracked .env.example. Added migration 0003 and trusted Store context operations:
canonical 4096-byte SHA256 artifacts, live-lease/version/stage/source checks,
atomic artifact/checkpoint/TRIAGING/event commit, owner/expiry/checksum reads.
Added authenticated local artifact GET and reducer guard requiring context
references before TRIAGING. Runtime cannot UPDATE/DELETE artifact rows.

Test-first unit run exited 1 for absent methods. Five new tests now pass, covering
invalid hashes/IDs before DB, missing-reference transition denial, real restart,
owner/API isolation, expiry/tamper checks, runtime rewrite denial and atomic
rollback on forced insertion failure. Final `uv run python -m
scripts.test_postgres` exited 0: 241 passed in 14.24s, including 17 real database
scenarios; two known third-party warnings remain. Ruff lint/format (52 files),
mypy (35 source files), lock check (36 packages) and wheel build exited 0.
Changed-source secret scan found zero findings. Dependencies unchanged; previous
vulnerability audit remains historical. Evidence: m1-artifact-tests.xml and
m1-artifact-checks.json. Historical PostgreSQL JUnit restored after preserving
the new harness output separately; old ledger hashes remain valid.

Limits: the worker must redact before storage; schema validation is not DLP or
source authentication. App owner checks are not tenant RLS. Seven-day read expiry
does not physically delete bytes; purge, configurable retention, backups,
encryption-at-rest and general manifests remain pending. Checksums cannot defeat
an administrator rewriting both digest and payload. Tests stored synthetic data
only; disposable DB/roles cleaned up. See docs/context-artifacts.md.

M0/M1 remain partial: collection graph wiring and live integration are unverified.
Next local task is graph/checkpoint recovery integration. Live fetch needs an
approved repo/commit and installation scope; sandbox/inference need explicit
charge approval. No cloud call, remote write or untrusted execution occurred.
New changes remain local and uncommitted.

## 2026-10-13 — Collection graph and domain-checkpoint recovery

Reread governing docs and actual changes. HEAD remains
e60e682128a1a7d10d1f5dc028ca9abb0adb76c7. Preserved existing implementation.
Added pinned LangGraph 1.2.15 and LangSmith 0.14.8 (context controls only), closed
CollectionDelta vocabulary, pure stage planning and one trusted commit reducer.
The bounded graph resumes from domain RunState, checks persisted scope/source,
collects sanitized context and stops at TRIAGING. No model/sandbox/GitHub dispatch.
Scope and collector failures persist safe failure_reason codes; queued/collecting
stages can fail closed. Hosted tracing is explicitly disabled, not environment
dependent. See docs/collection-graph.md and ADR-013 for domain/native limits.

Test-first collection exited 1 for absent module. Initial strict typing errors
were fixed using validated delta construction, named node inputs and explicit
StateGraph generic types. A failed local shell quoting attempt made no edit and
was replaced by apply_patch. Ten new tests cover pure planning/privileged-delta
rejection, real graph collection, completed replay without duplicate events,
injected interruption at COLLECTING, expired-lease replacement resume, old-worker
fencing, scope/context/source rejection, cancellation and tracing disabled even
when LANGSMITH_TRACING=true. This is injected graph interruption, not a claim of
native LangGraph checkpoint/task recovery; prior real process-death DB tests remain.

Final `uv run python -m scripts.test_postgres` exited 0: 251 passed in 17.73s,
including 23 real PostgreSQL scenarios. One existing AnyIO test-client alias
warning remains. Installing the graph's HTTPX2 dependency removed the previous
HTTPX compatibility warning without suppressions. Ruff check/format (55 files),
mypy (37 source files), lock check (63 packages), wheel build passed. New production
dependency audit: no known vulnerabilities. Changed-source secret scan: zero
findings. Explicit LangSmith pin added afterward without changing any resolved
version; lock/build rechecked. Evidence: m1-graph-tests.xml and m1-graph-checks.json.
Prior committed PostgreSQL JUnit restored to preserve historical ledger hashes.

M0/M1 remain partial: fixtures are synthetic preloaded snapshots, worker scheduling,
full incident ingestion, live authenticated fetch, native pending-task recovery,
cost/time reservations and exactly-once external action reconciliation are still
pending. Only pure planning is safely recomputed here. No complete agent workflow,
verified repair or deployment readiness claimed. Next: approved read-only source
adapter and background worker/input artifact wiring before real triage. Live
inference/sandbox still need charge authorization. No paid calls, remote writes
or untrusted execution occurred; changes remain local and uncommitted.

## 2026-10-14 — collection claim completion and bounded dispatch

Inspected the collection/storage contracts at source HEAD
`c10687e35c12e7fa1502c39e43ab577fe901ae93`. A regression reproduced completed
collection jobs being reclaimed. The saved red run has 252 tests and two failures
(reclaim regression and the tightened late-write error expectation).

Context persistence now closes its collection claim atomically. Added a one-job
dispatcher and deferred trusted input loader after scope admission, with safe
input failures and expired-worker fencing. Five additional PostgreSQL scenarios
exercise dispatch, denial, recovery, completion and expiry. No real source loader
or background polling service is claimed. See collection-worker.md for limits.

Final `uv run python -m scripts.test_postgres --evidence-name m1-worker-tests.xml`
exited 0: 256 passed, including 28 PostgreSQL scenarios, in 29.65s. One third-party
AnyIO deprecation warning remains. Ruff check/format (56 files), mypy (38 source
files), lock check (63 packages), wheel build and isolated worker import passed.
Changed-source secret scan found zero findings. No dependency versions changed;
the prior graph dependency audit remains historical, not a new audit run.

A stale shared JUnit output was detected before finalizing evidence. The harness
now accepts a validated dedicated evidence filename; the final fresh XML has zero
failures/errors. Earlier committed evidence was preserved. Current checks and
SHA256 are in evidence/m1-worker-checks.json; the red run is separately retained.

M0/M1 remain partial. Next: durable incident/input binding and an authenticated,
bounded read-only source adapter, then polling/exhaustion handling. Live provider
proof still requires explicit authorization for charges. No cloud calls, remote
writes or untrusted execution occurred. Changes remain local and uncommitted.

## 2026-10-14 — terminal collection lease exhaustion

Reread repository contracts and inspected current worker/storage implementation.
Prior worker changes were preserved. Source HEAD remains
`c10687e35c12e7fa1502c39e43ab577fe901ae93`; this slice is local/uncommitted.
The earliest unresolved local recovery defect was an exhausted job leaving its
run active indefinitely. The regression run reproduced CREATED after three
expired acquisitions: 255 passed, one failed, exit 1 in 23.94s.

The deterministic reducer permits infrastructure failure to INCONCLUSIVE before
collection. Claim reconciliation now atomically saves LEASE_EXHAUSTED, increments
the checkpoint version, clears the token and writes stage/exhaustion events.
Tests cover all four collection stages, repeated claims, stale heartbeat,
cancellation before reconciliation and rollback when the exhaustion audit fails.
Threat impact: no new credentials, permissions, outbound calls or model tools;
run/job locks and append-only events retain the existing concurrency boundary.

Final command `uv run python -m scripts.test_postgres --evidence-name
m1-exhaustion-tests.xml` exited 0: 261 passed, 33 PostgreSQL scenarios, 29.11s.
One existing third-party AnyIO deprecation warning remains. Ruff check/format,
mypy (38 source files), lock check (63 packages) and wheel build passed. A fresh
audit of the unchanged production graph found no known vulnerabilities; targeted
secret scan found zero findings. Commands, exit codes and artifact checksums are
in evidence/m1-exhaustion-checks.json. No UI exists to lint or smoke-test.

M0/M1 remain partial. Reconciliation requires a subsequent claim call; there is
no timer/polling daemon, and later-stage/legacy TRIAGING reconciliation is pending.
Durable incident binding and authenticated source loading are still needed.
The earliest milestone exit test remains live Nemotron/sandbox proof, requiring
explicit cloud-charge authorization. No paid provider calls, remote writes,
deployments or untrusted host execution occurred.

## 2026-10-14 — durable redacted incident intake and worker binding

Read governing specs and inspected intake, collector, worker, migrations and
related tests. Source HEAD: c10687e35c12e7fa1502c39e43ab577fe901ae93. Prior worker
and lease-exhaustion edits were preserved. The API discarded incident text after
hashing, preventing recovered workers from using the original report.

Added immutable bounded IncidentReport preparation and migration 0004. API
repository admission precedes redaction; the local bearer token and credential
patterns are removed before truncation. Run/report/event/job commit together.
Owner-scoped reads enforce retention, SHA256 and canonical schema. Runtime has
SELECT/INSERT only. Matching retries preserve the original report. Legacy
metadata-only runs fail closed instead of being backfilled.

bind_incident loads the retained report after scope admission and before source
I/O, then passes it through context redaction with the source trace. Synthetic
adapter tests prove recovery across Store reconstruction; they do not prove live
repository acquisition or a verified repair. Missing input stops source loading.

TDD: the first unit command exited 1 with the expected missing new module;
after implementation five unit cases passed. Storage slice: 268 passed in 29.27s.
Final `uv run python -m scripts.test_postgres --evidence-name
m1-incident-bound-tests.xml` exited 0: 270 passed, 37 PostgreSQL scenarios, 32.35s.
One existing third-party AnyIO warning remains. Ruff check/format (60 files),
mypy (40 source files), lock check (63 packages), wheel build passed. Fresh
unchanged-production dependency audit found zero known vulnerabilities. Targeted
secret scan returned one match at tests/unit/test_incident.py:10, reviewed as the
explicit synthetic redaction credential; no suppression was added and no real
credential was found. Evidence commands, exit codes and checksums are recorded
in evidence/m1-incident-checks.json. No UI checks apply to this backend slice.

Threat review: sanitized input is still untrusted and cannot grant execution;
raw report stays outside checkpoints/events and no public download route was
added. Patterns are incomplete DLP; arbitrary bare credentials/personal data may
survive. Seven-day expiry denies reads, without deleting backups or bytes.
Production confidentiality, purge and encryption remain unresolved.

M0/M1 remain partial. Next local dependency: bounded authenticated read-only
repository adapter and durable source input. Earliest live milestone acceptance
still needs explicit charge authorization for Nemotron/sandbox probes. No cloud
calls, remote writes or untrusted execution occurred; edits remain uncommitted.

## 2026-10-14 — bounded authenticated commit metadata resolver

Read governing specs and inspected repository grant/snapshot APIs and recent
changes. Source HEAD c10687e35c12e7fa1502c39e43ab577fe901ae93; prior local incident,
worker and exhaustion work preserved. GitHubReader adds one scoped authenticated
GET for an exact approved commit. Consent/owner/private admission occurs before
HTTP. The fixed origin, disabled environment proxies/redirects, bounded response,
deadline and safe errors prevent caller-controlled destinations or raw output.
No write path is exposed. Official GitHub/HTTPX sources and contract limits are
recorded in github-commit-resolver.md.

GitHub structured commit metadata is not raw commit bytes. The resolver returns
only correlated commit/tree identity and safe trace/time/policy metadata, with
raw_commit_verified=false and explicit TEST_TRANSPORT provenance in offline tests.
The existing snapshot validator was not changed or weakened. Token provisioning
must enforce Contents:read separately; this adapter cannot prove token scope.

Initial TDD command failed with the missing new module. Initial response fixtures
needed correction to avoid enormous pytest IDs and mock eager decompression;
no production checks were suppressed. Final focused tests: 20 passed in 0.13s.
Full `uv run python -m scripts.test_postgres --evidence-name
m1-github-resolver-tests.xml`: exit 0, 290 passed, 37 PostgreSQL scenarios, 31.86s.
One pre-existing AnyIO deprecation warning remains. Ruff check/format (62 files),
mypy (41 source files), lock check (63 packages), wheel build, fresh unchanged
production dependency audit and targeted secret scan passed; zero known
vulnerabilities or targeted secret findings. Commands and artifact checksums:
evidence/m1-github-resolver-checks.json. No UI checks apply.

M0/M1 remain partial. Live authenticated resolution is blocked on an operator
approved repository/commit and separately provisioned read credential; none was
discovered or used. Raw tree/blob acquisition, provenance integration and durable
snapshot storage remain next implementation work. M0 live inference/sandbox proof
still requires explicit charge authorization. No live GitHub requests, paid
provider calls, remote writes, deployment or untrusted execution occurred. Changes
remain local and uncommitted.

## 2026-10-14 — bounded REST tree/blob snapshots and provenance compatibility

Read governing docs and inspected actual snapshot/resolver, collector, storage
and tests at HEAD c10687e35c12e7fa1502c39e43ab577fe901ae93. Earlier local work and
unrelated untracked files were preserved. Added GitHubReader.acquire: admitted
commit metadata binds the root, then bounded non-recursive REST trees/blobs feed
the shared raw object/path validator. Original load_snapshot still requires the
raw commit hash. No commit bytes are synthesized from REST metadata.

Tree reconstruction uses Git ordering and mode normalization. Truncated trees,
tampering, invalid base64/size, symlinks, binary/LFS and denied directories fail
closed. Object reads retain prior count/decoded-byte/depth caps; REST encoding
has a separate 128 KiB response cap and acquisition progress deadline. Details
and official GitHub contracts are in github-snapshots.md.

Snapshot/context commit_identity preserves GITHUB_HTTPS_METADATA separately
from GIT_OBJECT_HASH. A real regression test exposed old canonical context
artifacts becoming unreadable after the new field: 302 passed, one failed,
exit 1 in 36.67s. Compatibility now checks the exact legacy form without changing
bytes/digests or dropping supplied provenance. Both new forms pass restart/API
owner, checksum and expiry tests. This does not establish signed authorship.

Initial acquisition tests failed seven times because acquire was absent. Final
focused suite: 51 passed in 0.19s. Final `uv run python -m scripts.test_postgres
--evidence-name m1-github-tree-tests.xml`: exit 0, 304 passed including 39 real
PostgreSQL scenarios in 37.82s. One existing third-party AnyIO warning remains.
Ruff check/format (63 files), mypy (41 source files), lock (63 packages), wheel
build/package membership, fresh unchanged-production dependency audit and targeted
secret scan passed. Zero known vulnerabilities or targeted secret findings.
Commands, exit codes and checksums: evidence/m1-github-tree-checks.json.

Threat impact: fixed authenticated GETs only, no URLs followed, shell, extraction
or repository execution. Live GitHub and token scope are unverified; fixtures are
explicit TEST_TRANSPORT. Full raw snapshots remain ephemeral and provenance
artifacts/production worker wiring remain pending. M0/M1 stay partial; live
repository acceptance requires approved scope and a separately provisioned read
credential, while M0 inference/sandbox proof requires charge authorization. No
live GitHub requests, paid calls or remote writes occurred. Changes are local
and uncommitted.

## 2026-10-14 — durable source manifests and bounded worker polling

Read governing specs and inspected implementation/tests at HEAD
c10687e35c12e7fa1502c39e43ab577fe901ae93; preserved all prior local work and unrelated
untracked files. Identified missing durable root/tree/file acquisition provenance
after the existing context handoff. Added bounded immutable SourceManifest and
migration 0005, storing only metadata (not source bytes, credentials or raw logs).

GitHub inputs now supply root/tree/blob/SHA256/time/trace/origin metadata. The graph
reducer commits it alongside context, source_manifest_ref, checkpoint event and
job completion. Storage checks run/repository/commit, persisted owner consent,
allowed paths, context origin and original file hashes. Observed-origin context
without a manifest is rejected. Owner-scoped API/storage reads enforce seven-day
expiry, SHA256 and canonical schema. Runtime SELECT/INSERT only; no UPDATE/DELETE.
Tests cover pipeline recovery/readback, owner isolation, rollback after manifest
insertion, scope mismatch, expiry, tamper and runtime permission denial.

First TDD command failed with the new manifest module absent; focused contracts
then passed 12 tests. Manifest slice: 333 passed, 46 PostgreSQL scenarios, 40.96s.
Continued with independently testable bounded polling over existing tick. serve
limits ticks/idle wait, accepts an Event-compatible stop, interrupts idle waits
and propagates worker errors without retry. It starts no daemon/configured loader.
Polling TDD initially failed with serve absent, then 10 focused tests passed in
0.68s; additional processed-job and real-DB completion-to-idle cases were included
in final validation. Shutdown is cooperative after an in-flight call, not forced
sandbox cancellation. See source-manifests.md and collection-worker.md.

Final `uv run python -m scripts.test_postgres --evidence-name
m1-durable-worker-tests.xml`: exit 0, 345 passed including 47 PostgreSQL scenarios,
41.93s. One pre-existing third-party AnyIO warning remains. Ruff check/format
(72 files), mypy (45 source files), lock (63 packages), wheel build, fresh audit
of unchanged production dependencies and targeted secret scanning passed; zero
known vulnerabilities/targeted findings. Actual commands, exit codes, source HEAD
and artifact checksums: evidence/m1-durable-worker-checks.json. No UI checks apply.

Threat review: manifests are metadata-only and owner-scoped; checksums do not
establish signed provenance or defeat privileged DB tampering. Legacy unobserved
fixtures remain supported without a manifest and do not establish live evidence.
Expiry is not deletion; deployment encryption/purge remain unresolved. Full raw
snapshot storage, sandbox packaging, process service/signal wiring and full live
worker acceptance are still pending. M0/M1 stay partial. Next: durable raw source
packaging/retention and worker service configuration. Live paid M0 proof remains
behind explicit charge authorization. No live GitHub requests, paid provider
calls, remote writes, deployment or untrusted execution occurred in this turn.
Changes remain local and uncommitted.

## 2026-10-14 — scoped source loader and composed collection pipeline

Reread governing documents and inspected current reader, graph, worker and tests
at HEAD c10687e35c12e7fa1502c39e43ab577fe901ae93. Prior local edits and unrelated
files were preserved. Added github_inputs, an operator-configured InputLoader
with explicit token and 1–16 distinct selected paths. No environment discovery
or automatic dispatch occurs. Persisted graph scope admission and retained input
precede source I/O; selected paths are checked again before acquisition. Each
loader call closes its HTTP client and supplies the read credential to redaction.

Closed SourceInputError outcomes route rejected source to POLICY_BLOCKED and
unavailable/auth/rate-limit/service input to INCONCLUSIVE. Only the graph reducer
commits these states through the existing lease fence, closing terminal jobs.
No denied action or unavailable request is automatically retried. New intake is
required after operator remediation. Unexpected programming errors still surface.

Snapshot and ContextBundle now retain evidence_origin from acquisition. Synthetic
HTTP remains TEST_TRANSPORT through stored context. Canonical legacy context
without provenance fields remains readable without changing stored bytes/digests;
missing origin is NOT_RECORDED. Both historical shapes are regression tested.

Initial focused TDD command exited 1 with SourceInputError absent; then eight
focused loader tests passed in 1.25s. Final `uv run python -m scripts.test_postgres
--evidence-name m1-source-pipeline-tests.xml` exited 0: 316 passed, including 43
PostgreSQL scenarios, in 40.52s. Composed tests cover real authenticated API intake,
persisted grants/report, reconstructed worker, synthetic REST tree/blob reads,
credential redaction and context readback; source denial/unavailability closes the
run without refetch. One existing third-party AnyIO warning remains. Ruff
check/format (65 files), mypy (42 source files), lock (63 packages), wheel build,
fresh unchanged-production dependency audit and targeted secret scan passed.
Zero known vulnerabilities/targeted secret findings. Commands, exit codes and
checksums: evidence/m1-source-pipeline-checks.json. No UI checks apply.

Threat review: fixed read-only broker paths and operator secrets only; no model
network tools, raw source/log checkpoints or code execution were introduced.
Context provenance does not verify signed authorship or repairs. Live token scope
is unverified, raw snapshots are ephemeral, and slow acquisition can lose its
lease; storage fencing rejects stale commits. See source-worker.md for composition.

M0/M1 remain partial. Next: durable source/provenance artifacts and worker service
configuration/scheduling. Live acceptance needs an operator-approved repository,
commit and separate read credential; paid M0 inference/sandbox proof needs explicit
charge authorization. No live GitHub requests, paid calls, remote writes or
deployment occurred. Changes remain local and uncommitted.

## 2026-10-14 — authorized live GitHub read and omission regression fix

Reread governing specs/current code at HEAD
c10687e35c12e7fa1502c39e43ab577fe901ae93, preserving existing local changes.
The user explicitly authorized Idk507/maf_ds_agent for read testing and allowed
available GitHub credentials. main resolved once to
08d828835fcb0cada16aca45c721ad0075e0c70e; subsequent adapter reads pinned that SHA.
No repository code was executed. Added offline-first scripts.check_github for
this fixed public repository and README.md only. CLI credential use is explicit,
captured in memory, shell-free and never printed/stored. Invalid live commit
arguments fail before credential retrieval; offline mode never retrieves tokens.

The first actual adapter attempt failed PATH_DENIED on .env.example root metadata.
An offline regression reproduced the failure. Portable metadata syntax is now
separate from content admission: excluded regular secret files are omitted without
fetching their blobs; allowed_entry still rejects those names. Traversal/reserved
paths, symlinks/submodules and object/hash limits remain enforced. Regression
cases cover .env, .env.example and secrets.key with absent forbidden blob objects,
proving omission cannot accidentally fetch their content. Existing negatives pass.

The post-fix live adapter command exited 0, PASSED/REMOTE_SERVICE. Root tree:
b70f64691e5ffc8a79286233fafe07c9e9f03460. README: 15629 bytes, SHA256
32673d68690246993d5990be48db183279fb1c4f6d29c2492717944a5e30c737.
Both safe failed/passed reports are retained separately. Credential least
privilege is not proven; repository metadata reports admin/push capabilities.
The broad available CLI credential was used only in this bounded GET smoke and
was not installed as a production worker secret. See github-live-smoke.md.

Final `uv run python -m scripts.test_postgres --evidence-name
m1-live-github-tests.xml`: exit 0, 324 passed, 43 PostgreSQL scenarios, 38.50s.
One pre-existing third-party AnyIO warning remains. Ruff check/format (68 files),
mypy (44 source files), lock (63 packages), wheel build, fresh unchanged-production
dependency audit passed with zero known vulnerabilities. Targeted source/report
secret scan flagged the same public commit SHA in both live JSON reports. Both
detector hashes were checked against SHA1 of that public identifier; no real
credential was found and no suppression was added. Initial feature/CLI/omission red
tests and actual command exit codes/checksums: evidence/m1-live-github-checks.json.

M0/M1 remain partial. Authenticated scoped file acquisition is now observed live;
least-privilege installation, durable source/provenance artifacts and full live
API/worker acceptance remain pending. Next implementation: durable artifacts and
worker service configuration. Paid inference/sandbox acceptance still requires
explicit charge authorization. Two live adapter invocations and two CLI metadata
queries performed reads only; no paid provider calls, remote writes, deployment
or untrusted host execution occurred. Changes remain local and uncommitted.

## 2026-10-15 — encrypted snapshot acceptance

Reviewed governing contracts and the implementation at source HEAD
355953dc81e5f7e455a3143aa9261ec7ea587ce3. Completed validation of the
opt-in encrypted source packaging/storage slice already present in this checkout.
AES-256-GCM with fresh nonces binds run and manifest; bounded restoration checks
consent, paths, sizes and both source hashes. Migration 0006 stores ciphertext
only. Graph reducer commits artifact references, context, manifests, event and
job completion atomically. Tests demonstrate rollback, restart, owner/expiry
checks, tamper rejection and runtime write restrictions. No key is auto-loaded;
no production key is provisioned. See encrypted-snapshots.md for threat limits.

Full disposable local PostgreSQL command exited 0: 360 passed, 52 PostgreSQL
scenarios, 54.61s, one existing third-party AnyIO deprecation warning. Ruff check
and format (75 files), strict mypy (46 sources), lock (66 packages), wheel build,
fresh production dependency audit and targeted secret scan passed. An initial
mypy invocation named nonexistent apps; corrected to services/packages/scripts
and passed without changes. Evidence/m1-encrypted-snapshot-checks.json records
commands and checksums. No UI exists to lint or run browser E2E against.

M0/M1 remain partial; no whole-milestone checkbox was promoted. Production key
provisioning/rotation/recovery and deletion/backup retention remain pending.
Next: establish operator key management before enabling retention, then finish
worker service configuration and remote replay packaging. Live inference and
sandbox acceptance need explicit charge authorization. No paid calls, remote
writes or repository execution occurred. Existing unrelated .env.example left
untouched. Documentation/evidence updates remain local; no merge is asserted.

## 2026-10-15 — explicit snapshot key rotation

Inspected specs, worker/source contracts, codec, storage tests and recent history
at HEAD 355953dc81e5f7e455a3143aa9261ec7ea587ce3; prior work preserved.
The earliest locally testable source-retention gap was rotation/recovery of
existing ciphertext when replacing a single encryption key. Added SnapshotKeyring
with 1–16 independent keys, one active write key, copied configuration and exact
historical key selection. Unknown/retired IDs fail closed, with no fallback or
key discovery. The scoped loader accepts the keyring without changing defaults.
New unit tests cover rotation, reconstructed instances, retirement, forged key
IDs, invalid configuration and copied input. PostgreSQL pipeline now additionally
covers rotated-keyring success, unavailable provider and tampered source.

Red test command failed collection because the keyring module did not exist.
Targeted codec/keyring/loader tests then passed 26 cases. Final full disposable
PostgreSQL suite passed 371 tests, 55 integration scenarios, 62.37s; one existing
third-party AnyIO warning. Ruff check/format (77 files), strict mypy (47 source
files), lock check (66 packages), wheel build, dependency audit and targeted secret
scan passed. Initial formatting errors were fixed with ruff format; no tests or
checks were weakened. Evidence: evidence/m1-key-rotation-checks.json and JUnit.

Threat review: rotation does not grant access or execute source. Caller/run/
manifest checks and AES-GCM authentication remain in SnapshotCipher. A forged
key ID cannot redirect decryption successfully. Keys are neither persisted nor
returned by API/graph; default repr excludes them. Independent key material is
required. Python memory erasure and privileged process access remain limitations.
No claim is made of durable key backup: reconstructed tests reuse test-only keys.

M0/M1 remain partial. Real provisioning/recovery requires an operator-controlled
secret store and stable key IDs; no production keys were generated or installed.
Next: choose/provision that trusted key source, then wire worker startup and
retention purge under a separate maintenance identity. Paid inference/sandbox
acceptance still needs explicit authorization. No cloud charges, remote writes,
untrusted execution, deployment or merge occurred. Changes remain local.

## 2026-10-15 — authorized live M0 probes and inference origin fix

Reread governing specs/tracker and inspected actual adapters, tests and history at
355953dc81e5f7e455a3143aa9261ec7ea587ce3. User explicitly authorized live charges,
continued use of the configured credential, and minimal use only when needed.
Exactly one catalog GET plus one fixed inference POST and one synthetic sandbox
operation were run. No automatic submission retries, image imports, private
source uploads, Tavily calls, GitHub writes or deployment occurred.

Inference command uv run python -m scripts.check_nebius --live exited 0:
Nemotron nvidia/nemotron-3-super-120b-a12b, HTTP 200, 22 input and 27 output tokens,
1672ms observed client latency. Cost unknown, not zero. Original safe report:
evidence/m0-live-inference-attempt.json. No response text retained. Trace is local,
not a provider request ID. The PowerShell Tee command saved only safe JSON.

An explicit in-memory operator invocation of SandboxConfig.from_env + run_smoke
used the process key, saved Contree project and previously selected Python image
UUID. It exited 0: PASSED/REMOTE_SERVICE, Python 3.12.13, UID/GID 1000, loopback
only, no Docker socket or credential environment names; terminal compute confirmed.
Operation 01a13f95-af3c-73cd-aa52-64eade3aa9c7. Normal completion needed no DELETE.
No secret was added to guest arguments or env. See m0-live-sandbox-attempt.json.

Found an inference evidence-label gap: injected and real clients had identical
report schemas with no origin label. Added required evidence_origin derived from
transport injection. Regression failed first (1 failed, 24 passed), then the
full offline/disposable PostgreSQL suite passed: 371 tests, 55 PostgreSQL cases,
83.00s, one existing AnyIO deprecation warning. Historical live JSON is unchanged;
no paid call repeated for this schema fix. An intermediate targeted invocation
named nonexistent test_sandbox.py and ran no tests; corrected via full suite.
Ruff/format 77 files, mypy 47 sources, lock 66 packages and wheel passed. Audit:
zero known vulnerabilities. Targeted scan: 2 existing synthetic test literals,
SHA1-checked against test-secret and secret; no actual key in reports, no suppression.

M0/M1 remain partial. Real inference and fixed guest execution are now observed;
full isolation/retention/deletion and production runner gates remain open. Current
provider security/limits docs require team confirmation for deletion/retention
needs. Next critical task: resolve sandbox capability guarantees or select a
remote runner that satisfies them before repository execution. Independent M1
operator key provisioning, service wiring and purge remain pending. Credential
exposure risk remains despite user-authorized use; no real credential is saved
in repo evidence. Changes are local; no merge is asserted. Full commands/hashes:
evidence/m0-live-acceptance-checks.json; scope: live-platform-acceptance.md.

## 2026-10-15 — bounded dependency provenance

Reread governing specs and relevant collector/artifact/graph/source documents;
inspected implementation/history at 355953dc81e5f7e455a3143aa9261ec7ea587ce3.
Preserved all prior local work. M0 full runner capability remains externally
unconfirmed; selected the earliest independent M1 context gap: dependency digest.

Added immutable dependency file identities and a canonical aggregate hash to
ContextBundle. Six exact root manifest names, only from the already admitted
snapshot, are recognized. Every body is capped and size/SHA256 checked, including
manifests not selected for model context. Only path/hash metadata is added;
no dependency resolver/install, host read, URL fetch or executable hook. Unknown
or absent approved manifests remain unknown (null digest), not an empty environment.
Metadata counts against existing context budget. Store binds dependency identities
to SourceManifest even when manifest contents are omitted. Older canonical stored
bundles missing these fields remain readable without digest/byte rewrite.

Initial unit tests: 3 failed, 1 passed before implementation. Targeted tests:
22 passed. Initial full suite: 375 passed, 2 failed (61.39s), because I appended
new tests after the existing must-run-last migration reset. The reset removed
runtime grants on recreated tables. Moved the unchanged reset scenario back to
last; did not broaden permissions or remove assertions. Final full command:
uv run python -m scripts.test_postgres --evidence-name
m1-dependency-provenance-final-tests.xml exited 0: 377 passed, 57 PostgreSQL cases,
61.43s, one existing third-party AnyIO warning. Failed JUnit retained separately.
Ruff check/format (78 files), mypy (47 sources), lock (66 packages), wheel build,
production dependency audit and targeted secret scan passed. Formatting errors
from initial edits were corrected before final acceptance. No UI exists for E2E.

Threat review: hashes bind identity, not dependency safety or completeness.
Only approved snapshot files are inspected; no new read scope or provider call
is introduced. No raw dependency content is added to metadata. Existing explicit
source-context selection still uses redaction. Canonical validation denies
unrecognized/duplicate/unordered dependency identities and aggregate tampering.
Store rejects mismatches before DB writes. Synthetic fixtures do not prove live
worker service operation. No DB migration was needed.

M0/M1 remain partial. Next critical external task: confirm required sandbox
resource/retention guarantees before repository execution. Next independent local
slice: explicit worker service startup/configuration, with production key source
and purge policy still unresolved. No cloud credential, inference, sandbox,
Tavily or GitHub call occurred this turn; local DB password used only for the
previously authorized disposable integration test database. No remote writes,
charges, deployment or merge. Evidence: m1-dependency-provenance-checks.json.

## 2026-10-15 — explicit local worker startup

Reviewed governing specs, current implementation/history and worker/source/DB
contracts at 355953dc81e5f7e455a3143aa9261ec7ea587ce3; prior changes preserved.
Selected the missing executable startup boundary on the shortest demo path.
Added scripts.run_worker: offline default performs no config/credential access or
DB/provider work. Explicit --run validates operator configuration, rejects remote
DB destinations and privileged/migration roles, then composes retained incident,
scoped source loader and existing bounded serve loop. Default one tick; max 1000.
SIGINT/SIGTERM cooperatively stop and prior handlers restore on every outcome.
CLI failures expose only safe codes, never raw DB/provider exceptions; no retries.
No dotenv/gh CLI/Nebius credential discovery or snapshot key auto-loading.

Config admits only explicit loopback DB host/user/database/password and bounded
connection fields. Explicit hostaddr prevents PGHOSTADDR environment redirection;
connect/SQL/lock timeouts are pinned. Paths are bounded, distinct and safe; runtime
persisted consent remains authoritative. Role preflight denies superuser, role/DB
creators and repository-grant writers before loader construction. This is not a
complete grant audit. Existing runtime permissions were not expanded.

Red: test_worker_startup collection failed before module existed. Targeted startup
and polling tests passed 20 before the final config masking case was added;
startup-only final tests passed 10. Full disposable DB suite exited 0: 391 passed,
61 PostgreSQL cases, 67.89s, one existing AnyIO warning. Integration uses synthetic
source only: incident-to-TRIAGING, pre-dispatch stop, sanitized unexpected failure,
handler restoration and migration-role rejection. Ruff check/format 80 files,
mypy 48 sources, lock 66 packages, wheel, dependency audit and targeted scan passed.
Initial formatting and make_conninfo typing issues corrected before acceptance.
Default CLI printed NOT_RUN; entry function returns 2, observed wrapper exit 1.
No provider access occurred. Evidence: m1-worker-startup-checks.json.

Threat review: configuration is operator input, not incident/model instructions.
No new authority to register grants, execute source, publish PRs, migrate schema or
encrypt retained source is exposed. Tokens/DSN use SecretStr and masked repr;
only safe polling counters cross output. Unexpected failures stop without retry,
leaving fenced lease recovery intact. Signal stop is not run cancellation.

M0/M1 remain partial. Next demo-path task: constrained reproduction/command and
patch contracts for the single fixture, followed by stage dispatch. Full runner
resource/deletion guarantees still need external confirmation before repository
execution; production read token/grants and supervisor remain operator setup.
Key management/purge are pending and retention stays disabled in CLI. No cloud
credentials/calls, GitHub reads/writes, charges, deployment or merge this turn.
Local PostgreSQL credential used only for authorized disposable test resources.

## 2026-10-15 — inert constrained pytest command planning

Reread specs/tracker and current runner ports, state transitions, budget and
repository contracts at 355953dc81e5f7e455a3143aa9261ec7ea587ce3. Prior work
preserved. M0 runner admission remains externally blocked, so implemented the
independent M2 command boundary needed for the single-fixture path.

Added frozen TestCommandRequest/TestCommandPlan and pure plan_pytest broker.
No process, shell, filesystem or provider call exists in this slice. Fixed
PYTEST_FILES_V1 argv; shell=false, no inherited env, plugin autoload disabled,
repository pytest config ignored and addopts cleared. Request admits only 1–16
safe approved tests/*.py paths, mandates regression target, exact source/run/
manifest/test refs and declared image hash. Baseline requires REPRODUCING and no
patch; candidate/clean replay require VERIFYING, baseline ref and latest patch ref.
Checks consent, cancellation and remaining command budget. Plans never authorize
execution or count as observed test results. Budget is checked, not reserved.

Initial tests failed collection before new contracts existed. Final targeted
command-policy suite passed 26 cases: injected paths, flags/unknown fields,
artifact/owner/stage changes, denied scope, cancellation, exhausted budget, stale
patches and forged argv/env/authority. Full disposable PostgreSQL suite exited 0:
417 tests, 61 PostgreSQL cases, 58.46s, one existing AnyIO warning. Ruff check,
format 83 files, strict mypy 50 sources, lock 66 packages, wheel, dependency audit
and targeted secret scan passed. No dependencies added. Evidence:
m2-command-plan-checks.json and m2-command-plan-tests.xml.

Threat review: tests/conftest are still untrusted code; disabling autoload/config
is not a security boundary. Artifact refs/image hash are declarations until a
trusted dispatcher validates bytes and runner identity. Current run/checkpoint
validation is not lease fencing or atomic budget reservation. Plan schema blocks
changes to fixed template/env and always denies execution authority. No existing
M0 adapter was expanded; production SandboxBackend remains deferred. Detailed
limits in command-planning.md. No model verdict or synthetic pass is promoted.

M0/M1 remain partial, M2 command planning is partial. Critical external boundary:
confirm resource/filesystem/network/retention guarantees and actual image identity
before repository execution. Next local task: bounded patch admission/application
and reproduction evidence contracts, then trusted artifact materialization and
stage dispatch. No cloud credentials, provider calls, charges, remote writes,
repository execution, deployment or merge occurred. Only local disposable DB
test credentials were used. UI/E2E repair acceptance remains unavailable.

## 2026-10-15 — strict patch admission before remote application

Reread governing specs/tracker and current snapshot, command, state and repository
contracts. Recent source HEAD: 14e20da52107160d1a77bfa8e74ef2368ff22cf7; earlier
command/foundation work now present in history. Preserved unrelated .env.example.
Selected the next independent M2 patch preflight boundary; runner admission is
still external and no executable operation is added.

Added pure services.runner.patches.admit_patch and frozen safe result metadata.
Requires current PATCHING state, consent, uncancelled run, remaining patch budget
and exact source/manifest/baseline/reproduction refs. Parses exact modify-only
Git unified diffs, derives paths/changed-line counts and hashes exact UTF-8 bytes.
Limits: 500,000 bytes, 10,000 lines, 25 files, 5,000 added/deleted lines. Only
approved src/app Python files allowed. Denies tests/workflows/lockfiles/secrets,
traversal, duplicates, creation/deletion, renames, binary/mode changes, malformed
or overlapping hunks, CRLF and NUL. No raw diff in result; no execution authority.
No files, processes, provider clients, database writes or budget reservations.

Red tests failed collection before module existed. Initial adversarial run had
18 passes and 2 harness errors while a 500 KB parameter became an automatic
pytest ID; short explicit IDs fixed the harness and prevent raw diffs entering
pytest test-name telemetry. Intermediate 19 cases passed. Expanded final patch
suite: 30 passed. Initial Ruff long lines/import order fixed before acceptance.
Full disposable DB command exited 0: 447 passed, 61 PostgreSQL scenarios, 63.18s,
one existing third-party AnyIO warning. Ruff/format 85 files, strict mypy 51
sources, lock 66 packages, wheel build, production dependency audit and targeted
secret scan passed. No dependencies changed. Evidence: m2-patch-admission-checks.json.

Threat review: parsing is admission for a future check, not application/safety
proof. Hunk context is not compared to source bytes and file existence is not
established. Actual git apply --check and application remain mandatory in a fresh
admitted remote sandbox. Source/test/baseline refs are bindings, not validated
observations; trusted artifact retrieval, atomic budget/fencing/cancellation and
runner identity checks remain required. All tests use synthetic source text.
A patch can still introduce arbitrary Python behavior and must pass independent
static/security/regression/clean-replay gates. M0 probe remains unchanged.

M0/M1/M2 remain partial. Next: reproduction/baseline evidence validation and
trusted artifact materialization, then fixed remote check/application/dispatch
once runner resource, filesystem, image and retention guarantees are confirmed.
No local source execution, cloud credential access, provider calls, charges,
remote writes, deployment or merge occurred. Local PostgreSQL password was used
only for previously authorized disposable integration tests. Changes are local.

## 2026-10-15 — bounded unauthenticated baseline inspection

Reread governing specs/tracker and inspected command plans, patch gate, runner
contracts and recent code at 14e20da52107160d1a77bfa8e74ef2368ff22cf7. Prior local
patch-admission changes preserved. Implemented the next independent M2 gap:
qualifying a supplied baseline report without mistaking setup faults for proof.

Added FailureExpectation and inspect_baseline. Checks exact baseline plan phase,
report SHA256, strict exit code 1, regression classname/function, one failed target,
AssertionError plus bounded explicit marker, no other failures/errors/skips and
consistent suite/root counts. XML is UTF-8 only, capped at 128 KiB/2048 nodes/
64 depth/16 suites/256 cases. DTD/entities, nested outcomes/metadata, duplicate
cases, malformed/oversized reports and syntax/import/collection traces fail closed.
Only safe refs/bindings/reasons/time/trace leave the function; no raw XML, marker,
failure text or output logs. No filesystem/process/network or DB writes occur.

Every result remains UNAUTHENTICATED_INPUT with can_authorize_patching=false.
MATCHED_ASSERTION means supplied XML structurally matches the expected failure,
not that any remote execution happened or reproduced the incident. No graph edge,
state mutation or baseline ref setter was added. Trusted runner receipt validation
and actual artifact retrieval remain required; no synthetic pass is promoted.

Red unit collection failed before module existed. Initial targeted suite passed
16 cases, expanded suite 22. Initial formatting/type issues corrected before final
checks. Full disposable PostgreSQL command exited 0: 469 passed, 61 PostgreSQL
scenarios, 70.48s, one existing AnyIO deprecation warning. Ruff check/format 87 files,
strict mypy 52 sources, lock 66 packages, wheel, dependency audit (zero known
vulnerabilities) and targeted secret scan (zero findings) passed. No dependencies
added. Evidence: m2-baseline-inspection-checks.json and corresponding JUnit.

Threat review: untrusted XML cannot trigger external resolution, expansion or
source execution. Global byte bounds precede parsing; iterative node/depth checks
and flat outcome validation bound interpretation. A fabricated report can match,
so authentication is explicitly absent and cannot grant patch authority. Marker
matching alone is not business-cause proof. Parser failures never echo input.
Raw workload reports are not automatically retained or sent to any model.

M0/M1/M2 remain partial. Next critical integration: authenticated/correlated runner
receipts, exact source/test/patch/report artifact retrieval and trusted reducer
handoff. Actual baseline/candidate/application/clean replay still blocked on full
runner resource/filesystem/image/retention admission. No cloud credentials or
provider calls, charges, remote writes, repository execution, deployment or merge
this turn. Local PostgreSQL password used only for authorized disposable tests.

## 2026-10-15 — signed baseline receipts and report binding

Continued the next step at source HEAD 14e20da52107160d1a77bfa8e74ef2368ff22cf7,
inspecting current baseline, command and repository contracts; preserved prior
patch/baseline local work. Applied test-driven workflow; no bug-free guarantee.

Added ExecutionReceipt/SignedReceipt, explicit ReceiptVerifier public-key registry
and signed-baseline inspection wrapper. Canonical JSON signs purpose/version,
algorithm/key ID and payload. Hashes exact full command plan and signed envelope.
Verifies signature, configured origin, run/plan/execution, pre/post source tree,
report hash, aware clock (300-second age, five-second future skew), terminal state,
no timeout/signal and baseline exit 1. Wrapper hashes actual bounded JUnit bytes
before signature correlation and existing structural/failure inspection.

Registry is copied, bounded to 16 explicit public keys; no signing/key discovery,
credentials, filesystem or provider access. Tests use ephemeral private keys and
TEST_TRANSPORT only. No production signer/key installed. Verified wrappers retain
can_authorize_patching=false: signer identity is not runner capability or business
behavior proof. Same-action verification is idempotent; no one-use replay cache
or durable action consumption is claimed. Expectations must be from trusted action
records, not a model or guest. Producer must live outside workload and independently
observe facts, redact report bytes and sign their exact hashes. No graph write added.

Red tests failed collection before module existed. First implementation run:
14 passed, 2 failed due duplicate now argument in test helper; corrected helper,
then 16 passed. Expanded suite: 22 passed. Full disposable PostgreSQL command
exited 0: 491 passed, 61 PostgreSQL scenarios, 145.71s; one existing AnyIO warning.
Ruff check/format 89 files, strict mypy 53 sources, lock 66 packages, wheel,
dependency audit (zero known vulnerabilities) and targeted secret scan (zero
findings) passed. Initial long-line issues formatted before final acceptance.
No dependencies added. Official cryptography Ed25519 sign/verify docs checked;
existing pinned package used. See runner-receipts.md and m2-runner-receipt-checks.json.

Threat review: signature covers domain purpose, algorithm/key ID and report/source/
plan bindings; tampering or other expected executions rejected. Signing keys never
enter library/guest data. Signature verification authenticates only configured
signer claims. XML remains bounded untrusted content; fake signed statements under
a compromised/misconfigured key are not hardware attestation. Key registration,
revocation, signer deployment, source inventory semantics and live capabilities
remain operational requirements. No model can use this module to commit a verdict.

M0/M1/M2 remain partial. Next: durable action/receipt storage and idempotent fenced
acceptance, trusted artifact retrieval/materialization and production collector
identity, followed by actual runner admission/execution. No cloud credential use,
provider call, charge, GitHub write, repository execution, deployment or merge.
Local PostgreSQL password used only for authorized disposable regression tests.

## 2026-10-15 — durable baseline journal and evidence recovery

Inspected current run/job/storage, receipt and command contracts at HEAD
14e20da52107160d1a77bfa8e74ef2368ff22cf7; prior local changes preserved.
Applied incremental/test-driven workflows. Migration 0007 adds append-only baseline
actions, receipts and correlated audit rows. Runtime SELECT/INSERT only; no model
API, dispatcher, budget debit or graph/stage mutation is introduced.

BaselineAction stores exact canonical plan, execution ID, expected tree and failure
expectation under 16 KiB/hash checks. Writes lock run/job, check owner/live fence,
version/REPRODUCING/cancellation and persisted scope/source/test bindings. Unique
execution and run/plan identities reconcile exact repeats and reject conflicts;
six planned actions maximum per run. Action+PLANNED audit commit atomically.
Receipt retention loads expectations from DB, checks real JUnit bytes/signature
with DB clock and commits safe receipt/inspection metadata+audit atomically. No
raw report/source/log or signing key is retained. Owner/expiry/schema/checksum/
canonical reads support restart. Historical reads are not current-key approval.

Red schema test failed collection before module existed. Model test then passed.
Initial full suite passed 501 tests/70 PostgreSQL cases, 75.55s. Review identified
that retaining a completion incorrectly reused a fresh-command budget gate. Added
a real DB regression: targeted run failed COMMAND_BINDING_DENIED at budget zero
(1 failed, 501 deselected, 3.52s; artifact retained). Fixed retention to check scope
and immutable bindings independently; it cannot start work or refund budget. New
planning remains budget-gated. Final full command exited 0: 502 passed, 71
PostgreSQL scenarios, 68.28s; one existing AnyIO warning. Cases cover concurrent
replay/restart, owner/version/lease conflict, tampering/expiry, no runtime DELETE,
audit rollback and completion after last command debit. Fixture migration role
explicitly seeds synthetic REPRODUCING state; runtime graph progression is not
asserted. Last migration reset remains last test.

Final Ruff/format 92 files, mypy 54 sources, lock 66 packages, rebuilt wheel,
production dependency audit (zero known vulnerabilities) and targeted final secret
scan (zero findings) passed. Initial long SQL line formatting and mixed newline
issues corrected; no suppressions or test weakening. No dependencies added.
Evidence: m2-baseline-journal-checks.json, separate initial/red/final JUnit files.

Threat review: no external operation follows journal insertion. Source/report
signatures bind configured signer claims, not safe runner capabilities. Current
scope and fences guard retention without requiring another execution budget.
Checksums cannot protect privileged DB tampering; reads must be independently
reverified before security decisions. Seven-day expiry is not deletion. Audit
contains IDs/version/trace/worker/time, no workload text. Signature freshness
applies to retention retries; recovery after freshness reads prior immutable data.
SSE exposure, dispatched/unknown-provider reconciliation and atomic budgeted intent
remain future work. Checkpoint unchanged; PATCHING stays closed.

M0/M1/M2 partial. Next: budgeted durable dispatch and trusted artifact materialization
with production collector identity and runner capability admission. No cloud keys,
provider calls, charges, GitHub writes, repository execution, deployment or merge.
Only previously authorized local disposable PostgreSQL credentials were used.
# 2026-10-16 — Atomic baseline dispatch intent reservation

Local unmerged slice based on 14e20da52107160d1a77bfa8e74ef2368ff22cf7.
Migration 0008 and BaselineJournal.reserve_dispatch atomically record execution
intent, debit one sandbox command, increment the checkpoint version and emit a
budget audit event. Exact concurrent retries reconcile without another debit.
Owner, current consent/bindings, live lease, cancellation, stage and initial
version checks remain mandatory. A completed action cannot reserve new work.
There is no external dispatch, execution permission, refund or automated retry.

Acceptance: 509 tests passed in 66.34s, including 78 real PostgreSQL scenarios;
one upstream AnyIO deprecation warning. Ruff check/format (93 files), mypy
(54 sources), lock validation (66 packages), wheel build, dependency audit
(zero known vulnerabilities) and targeted secret scan (zero findings) passed.
The full initial run exposed a test expecting an unsanitized database error;
expanded negative tests exposed two test setup mistakes. These were corrected,
and failed reports are retained alongside final evidence. Formatter normalizes
the integration file's mixed line endings; unchanged test logic is preserved.
Commands, exit codes, source hashes and reports:
docs/evidence/m2-dispatch-reservation-checks.json.

M2 remains partial. Provider operation identity/reconciliation, production signer,
runner admission, executable baseline/patch replay and reducer stage acceptance
remain next. No cloud credentials, billable service or remote write was used.
# 2026-10-16 — Bounded provider-operation recovery journal

Local unmerged slice based on 14e20da52107160d1a77bfa8e74ef2368ff22cf7.
Migration 0009 and OperationJournal retain at most three immutable lifecycle
assertions per reserved execution: SUBMISSION_UNKNOWN, OPERATION_KNOWN and
COMPUTE_TERMINAL. They bind the action hash, owner/run, provider/operation UUID,
trace, worker, checkpoint version and DB timestamp. Unique operation identity,
live lease/version fencing, bounded ordered records and canonical hash checks
support recovery without refunding budget or scheduling another operation.
Cancellation permits later binding/terminal metadata under a live lease, but
cannot start a new submission assertion. No stage or baseline result is changed.

Acceptance: intentional red collection failed before the module existed; the ten
unit contract cases then passed. Thirteen focused PostgreSQL scenarios passed;
after adding rollback, permission and final-command cases, the complete suite
passed 534 tests including 93 PostgreSQL scenarios in 84.23s (exit 0). One upstream
AnyIO deprecation warning remains. Ruff check/format (97 files), strict mypy
(55 sources), lock validation (66 packages), wheel build, diff whitespace and
targeted secret scanning (zero findings) passed. Dependency inputs are unchanged;
the preceding m2-dispatch-reservation-dependency-audit.json reports zero known
vulnerabilities and is reused, not represented as a new scan.

Commands, exit codes, file hashes and test artifacts are recorded in
docs/evidence/m2-operation-journal-checks.json. Threat boundary and recovery
semantics are documented in docs/operation-reconciliation.md. All lifecycle
assertions are UNVERIFIED_ADAPTER_INPUT and cannot authorize execution. Live
lookup/polling, one-use submission controller, authenticated runner evidence,
cleanup after lease completion and physical purge remain pending. No provider
credentials, cloud execution, charge or remote write was used. M2 stays partial.

## 2026-10-16 — Signed runner capability gate and configuration drift

Local unmerged slice based on 14e20da52107160d1a77bfa8e74ef2368ff22cf7.
CapabilityVerifier authenticates a bounded independent assessor profile scoped
to runner UUID, backend, exact OCI image, immutable runtime configuration digest,
origin, policy and freshness. Twenty-two evidenced controls and conservative
enforced limits must pass; unknown/failed/missing controls, test-only evidence and
excessive caps deny. Successful capability validation cannot authorize execution.
The existing synthetic M0 adapter and probe remain intact; no dispatcher or
production assessor key is added. See docs/runner-admission.md for threat impact,
resource choices, attestation limitations and the required external evidence.

Initial red collection failed before implementation. The first full suite passed
630 tests, but review identified missing runtime-configuration binding: the new
regression failed with DID NOT RAISE. Adding a required signed configuration hash
and trusted expected hash corrected it. Final unit acceptance passed 97 tests;
the final complete suite passed 631 tests including 93 PostgreSQL scenarios in
81.85s, exit 0. One upstream AnyIO deprecation warning remains.

Ruff check, format (99 files), strict mypy (56 sources), lock validation
(66 packages), final wheel build, diff whitespace and targeted secret scan
(zero findings) passed. Dependency inputs remain unchanged; the prior
m2-dispatch-reservation-dependency-audit.json (zero known vulnerabilities) is reused
and is not presented as a fresh scan. Commands, exit codes, failed regression,
source hashes and actual reports: docs/evidence/m2-runner-admission-checks.json.

M2 remains partial. Production assessor/key provisioning, artifact resolution,
actual runtime configuration verification, live capability assessment, controlled
submission and genuine baseline/patch replay remain pending. Provider/alternative
remote-runner evidence is required to enable repository execution; the M0 smoke
cannot waive missing image/resource/filesystem/retention controls. No cloud
credentials, provider execution, charges or remote writes were used.

## 2026-10-16 — Durable time and estimated-spend budget exhaustion

Each run checkpoint now carries bounded remaining action counts, 900 seconds of
active execution time and 2,000,000 USD micro-units of estimated spend. The
generic trusted reservation path accepts only positive integer quantities. Once
a resource is already exhausted, it atomically stores `BUDGET_EXHAUSTED`, moves
the run to `INCONCLUSIVE`, finishes the worker lease and emits the existing
stage-transition audit event. This prevents a caller from treating a denied
reservation as a retryable provider action. Legacy checkpoint JSON remains
readable because the new fields have conservative defaults.

An initial PostgreSQL acceptance run found that adding a new event type broke
migration downgrade in databases containing that event. The final design keeps
the durable failure reason in the checkpoint and uses `STAGE_CHANGED`, preserving
the established reversible event schema. The final disposable-PostgreSQL suite
passed 638 tests including 95 PostgreSQL scenarios in 83.55 seconds; focused
unit tests, Ruff, formatting, strict mypy, lock validation, wheel build and a
targeted secret scan passed. Evidence: `m2-budget-exhaustion-checks.json`.

M2 stays partial: amounts are run-level declared caps, not verified provider
prices. A future controlled submission controller must bind actual provider
cost/time estimates and reserve them with dispatch intent before remote work.
No provider credential, cloud operation, charge or remote write was used.

## 2026-10-16 — Live checkout baseline reproduction

The synthetic checkout-retry fixture was materialized through the Contree SDK
into one disposable `python:3.12-slim` sandbox and run with standard-library
unittest. It exited 1 after 3.766 seconds with the intended behavioral failure:
the retry assertion observed two charges where one was required. The source
workspace digest is `c4264ba39327b109942fff18c7fb9cd55966a511269d0a6553fbfeb632d60209`.
The full structured evidence and limitations are recorded in
`m2-checkout-baseline-live.json`.

This validates baseline reproduction only. Image identity remains a provider tag,
the direct adapter call is not yet recorded in the durable operation journal, and
no candidate patch, oracle run, clean replay, GitHub operation or PR occurred.

## 2026-10-16 — Live candidate regression and oracle checks

A minimal candidate copy records completed checkout IDs before charging. Two
separate disposable Nebius sandboxes ran the visible unittest regression and
the oracle unittest; both returned exit code 0 in 3.718 and 3.953 seconds.
The candidate workspace digest is
`24346b807fb5c3e577ce7519a783aa32b99f094f5fef65f4f0c240c02d1479bc`.
Evidence and limitations are recorded in `m2-checkout-candidate-live.json`.

The candidate is an explicit fixture copy, not a model-generated Git-applied
patch. The oracle remains locally visible, clean replay and deterministic gates
are absent, and the direct SDK calls do not yet create durable operation records.

## 2026-10-16 — Live clean replay

A third, fresh disposable Nebius sandbox replayed the candidate visible
regression and returned exit code 0 in 3.187 seconds with the same candidate
workspace digest. Evidence is recorded in `m2-checkout-clean-replay-live.json`.
This is repeatable execution evidence only: it cannot set `VERIFIED` until
durable operation correlation, verified image identity, hidden-oracle isolation
and deterministic gates are implemented.

## 2026-10-17 — Deterministic verifier binding checks

The local deterministic gate evaluator now accepts only typed runner/verifier
observations and requires each mandatory gate to agree on one workspace digest,
immutable source commit and candidate patch digest. Missing, failing, duplicate
or mixed observations fail closed as `INCONCLUSIVE` or `NOT_VERIFIED`; a model
origin is rejected by the input contract. Evidence manifests are rebuilt from
the supplied bytes before they are trusted, so a changed artifact name or byte
payload fails verification.

`uv run pytest -q` passed with 571 tests passed and 95 skipped (exit 0).
`uv run ruff format --check services/verifier tests/unit/test_verifier_gates.py
tests/unit/test_evidence_manifest.py`, `uv run ruff check` on the same paths,
and `uv run mypy services/verifier` also passed (exit 0). One upstream AnyIO
deprecation warning was reported. This is offline evidence only: runner
receipts, durable artifact storage/access control and the clean-replay inputs
are not yet wired to these gates, so M3 remains incomplete. No provider call,
cloud charge or remote write was made.

## 2026-10-17 — Bounded live Nemotron triage attempt

One authorized, no-retry Nemotron request used a 4 KB-bounded synthetic checkout
fixture context with a 600-token response cap. The Token Factory endpoint
responded, but its completion did not validate against ReplayForge's strict
`TriageProposal` schema, so the gateway returned `TRIAGE_SCHEMA_INVALID` after
3.719 seconds. The sanitized response metadata is retained in
`m2-live-triage-attempt.json`; neither the prompt nor completion content nor any
credential was written to disk or logs.

This is a safe rejection, not a generated reproduction candidate. The M2
Nemotron/reproduction acceptance remains incomplete. No remote write or sandbox
execution occurred, and no retry was attempted to avoid unneeded provider cost.

## 2026-10-17 — Triage prompt contract hardening

The bounded triage gateway now sends an application-owned system instruction
before its JSON-encoded evidence. It defines the exact `TriageProposal` shape,
prohibits Markdown and extra keys, requires a `tests/*.py` reproduction path,
and states that the model must treat all supplied context as untrusted evidence.
The typed gateway return is now `TriageProposal`; it still has no retry logic and
does not retain prompt, completion, or credentials.

Focused gateway/parser/contract checks passed (13 tests), including a prompt
injection case that proves untrusted incident text cannot replace the system
schema. The complete local suite passed with 572 tests passed and 95 skipped;
Ruff formatting/check and strict mypy for the affected modules passed. This did
not invoke a provider, execute a sandbox, or make a remote write. The live M2
reproduction acceptance remains pending a separately authorized retry.

## 2026-10-17 — Reproduction-test artifact contract

`ReproductionProposal` now carries bounded Python `test_source` in addition to
its target path, behavior, and expected failure signature. The control plane
only parses the source with `ast.parse`, rejects NUL and malformed syntax, and
computes a SHA-256 content identity for later remote-runner admission. It does
not execute, import, or otherwise evaluate model-proposed code on the Windows
control plane.

Focused repair-contract, parser, and gateway checks passed (15 tests). The
complete local suite passed with 574 tests passed and 95 skipped. Ruff check,
format, strict mypy, and whitespace validation passed. This prepares the M2
live acceptance but does not satisfy it: an authorized model call must still
return a schema-valid artifact, then an admitted remote runner must execute it.
No provider call, cloud charge, sandbox execution, or remote write occurred.

## 2026-10-17 — Second bounded live reproduction attempt

One authorized no-retry Nemotron call used the hardened schema and asked for a
syntactically valid regression-test artifact for the synthetic checkout fixture.
The endpoint responded, but the completion again failed the strict proposal
contract after 3.765 seconds. The sanitized metadata is retained in
`m2-live-triage-reproduction-attempt.json`; prompt, completion, and credentials
were not retained. No test artifact was accepted, no sandbox ran, and no remote
write occurred. M2 acceptance remains incomplete.

## 2026-10-17 — Provider structured-output compatibility finding

A third authorized no-retry call returned no usable text content and was safely
classified as `TRIAGE_OUTPUT_SIZE` after 3.719 seconds. ReplayForge's parser
now distinguishes empty/non-text, malformed JSON, non-object JSON, missing
required fields, and invalid contracts without retaining provider text. The
Token Factory documentation index advertises structured JSON support, but this
model/request combination did not yield content with the provider-side
`response_format` flag. The gateway therefore relies on its application-owned
JSON instruction and local Pydantic validation instead of that flag. This does
not relax the schema or permit malformed output.

The subsequent normal chat-completion retry also returned no usable text after
3.203 seconds (`m2-live-triage-normal-chat-attempt.json`). After four bounded,
no-retry live attempts, ReplayForge stops rather than spending credits on blind
retries. No candidate was accepted or executed. Further live generation needs a
provider response-shape investigation or a confirmed compatible Nemotron model
route; the M2 task remains blocked and unchecked.

## 2026-10-17 — Triage completion-envelope validation

The triage gateway now accepts a response only when the provider choice has
`finish_reason == "stop"` and a string final-content field. Length-limited
choices, missing content, non-string content, and malformed choice envelopes
fail closed with distinct sanitized errors before any model text reaches the
proposal parser. This prevents an incomplete generation from being misreported
as a schema-valid repair artifact.

Focused triage checks passed (21 tests), including each malformed envelope
case. The complete local suite passed with 580 tests passed and 95 skipped;
Ruff formatting/check, strict mypy, and whitespace validation passed. No
provider call, sandbox execution, remote write, or cloud charge occurred.

## 2026-10-17 — Documented strict structured-output request

The triage gateway now requests the documented named `json_schema` wrapper
whose strict schema is generated from the local frozen `TriageProposal` model.
It also sends `store: false`, matching the Token Factory chat-completion API
example. Local Pydantic validation remains mandatory after the response is
received. The provider documentation says structured output support varies by
model, so this configured Nemotron model has not been marked compatible without
a separately authorized, single live capability test.

Focused triage gateway/parser/contract tests passed (22 tests). The complete
local suite passed with 581 tests passed and 95 skipped. Ruff formatting/check
and mypy for `packages` and `services` passed; `git diff --check` passed. No
provider call, sandbox execution, remote write, or cloud charge occurred. The
M2 real-inference acceptance remains unchecked because no runnable candidate
has been observed.

## 2026-10-17 — Strict-schema live capability attempt

One newly authorized, no-retry request used the documented named strict
JSON-schema format with `store: false` and a tiny synthetic checkout-retry
context. The configured `nvidia/nemotron-3-super-120b-a12b` endpoint responded
after 5.172 seconds, but its choice did not finish with `stop`; the gateway
therefore rejected it as `TRIAGE_OUTPUT_INCOMPLETE`. Sanitized metadata is in
`m2-live-triage-strict-schema-attempt.json`. The prompt, completion, generated
test source, and credential were not retained.

No candidate was accepted, no sandbox ran, and no remote write occurred. The
M2 acceptance remains unchecked. A further capability attempt requires a fresh
authorization because it would incur additional inference usage.

After the attempt, `uv run pytest -q` passed with 581 tests passed and 95
skipped. Ruff formatting/check, mypy for `packages` and `services`, whitespace
validation, and a source secret scan passed; the scan explicitly excludes the
intentional `synthetic-api-key` redaction fixture. One upstream AnyIO
deprecation warning was reported.

## 2026-10-18 — Endpoint-compatible triage JSON mode

Review of the Token Factory endpoint reference found that its current
`chat/completions` request schema lists only `json_object` and `text` response
formats. That conflicts with the separate structured-output guide, which shows
`json_schema` examples for other model families and advises checking a model's
JSON-mode capability. ReplayForge is pinned to a Nemotron model with no verified
JSON-schema capability, so the triage adapter now requests `json_object` and
keeps the application-owned prompt plus local frozen `TriageProposal` validation
as the strict acceptance boundary. The prior `json_schema` request was removed
before any further live call.

Focused gateway/parser/contract tests passed (22 tests). Ruff formatting/check,
mypy for the affected files, and whitespace validation passed. This is an
offline compatibility correction; it does not make the M2 model-generation
acceptance complete and does not consume provider usage.

The complete local suite then passed with 581 tests passed and 95 skipped. One
upstream AnyIO deprecation warning was reported.

## 2026-10-18 — JSON-object live capability attempt

### Baseline-correlated patch controller

Added `run_patch` to services/orchestrator/repair_flow.py. It reads the current
owner-scoped checkpoint, retained baseline receipt and action, requires their
canonical digest to equal baseline_result_ref, and checks remote origin plus
run/repository/commit/source-manifest/reproduction/plan bindings. TEST_TRANSPORT
evidence is rejected before reservation or network access. PATCHING must already
be established by the trusted reducer; this controller cannot open that stage.

Only persisted sanitized app/src Python context is loaded. Conflicting duplicate
paths and oversized contexts fail before model reservation. After a single
sticky reservation the gateway makes at most one request and the patch journal
re-admits/retains its result. Re-entry at VERIFYING checks the retained artifact
and returns without submitting again. Caller-supplied failure prose is not used;
the bounded failure marker comes from the retained baseline action.

Four contract tests use explicitly synthetic signers, service ports and HTTP
responses (not live remote claims). They cover success/restart reconciliation,
test-origin denial, wrong baseline reference and wrong source commit, including
zero requests/reservations on rejection. `uv run pytest -q`: 609 passed,
103 PostgreSQL-dependent skips, one upstream warning, exit 0. Ruff check/format,
mypy packages/services and whitespace validation passed. The prior real DB
journal suite remains 708 passed; no persistence schema was changed here.
No provider call, cloud charge or remote write occurred. M2 remains partial:
the controller cannot replace missing admitted runner/baseline reducer evidence,
exact spend/request binding, remote git-apply and candidate replay.

### Durable one-shot patch attempt lifecycle

Final full disposable local PostgreSQL suite: 708 passed, zero skips, one
upstream AnyIO deprecation warning in 84.31 seconds, exit 0. Evidence:
`m2-patch-journal-final-tests.xml`. Migration downgrade/reapply passed.
Repository-wide Ruff check/format, mypy packages/services and whitespace
validation passed. These are local persistence/contract results, not live
generated-patch application acceptance.

Added migration `0012` and `services/persistence/patch.py`. A live owner-scoped
lease, exact checkpoint version, current consent and PATCHING evidence bindings
are mandatory. Reservation atomically debits one model call, increments version,
stores uncertain submission intent and emits BUDGET_RESERVED. Existing intent
denies a second submission even after restart. This conservative first version
supports one patch inference attempt per run; failed/unknown outcomes are not
refunded or resubmitted.

Retention revalidates the actual proposal and deterministic diff admission,
requires an exact file-claim match and the reserved checkpoint version, and
commits bounded canonical bytes, a patch-attempt reference, one patch-attempt
debit, VERIFYING stage and event together. Reads verify owner, seven-day expiry,
canonical checksum, actual diff hash and checkpoint/source/baseline/test
bindings. Neither stage transition nor retention is evidence of application,
remote execution or a verified repair. Runtime roles get SELECT/INSERT only.

Focused disposable PostgreSQL tests: two passed, proving restart read,
duplicate denial without a second debit, and candidate/checkpoint rollback when
the event write fails. Evidence: `m2-patch-journal-focused.xml`. No cloud call,
secret loading or remote write occurred. Exact model request/spend/time binding,
full patch-controller integration and admitted runner dispatch remain pending.
Base commit ffcafe2fae11648f3390a647201da4df9a3cffd2; uncommitted local work.

### Bounded patch-proposal gateway

Added the closed PatchProposal contract and packages/providers/patch.py.
The single-call gateway requires PATCHING, consent, baseline/reproduction/source
references and patch budget; admits only bounded sanitized app/src Python
context. Model output must be complete JSON matching the contract, then pass
the existing deterministic modify-only diff broker. Claimed paths must match
the parsed diff and belong to the supplied context. Test modifications, extra
fields, malformed diffs and truncation are denied. This code never applies a
patch, runs code, advances a run or verifies a repair.

The gateway uses the already live-tested non-reasoning bounded profile:
1200 max_completion_tokens, reasoning_effort=none, JSON-object, store=false,
no redirects/proxy inheritance and no retries. Response bytes are bounded while
streaming. Exact route/configuration is revalidated before attaching the key.
Reference: https://docs.tokenfactory.nebius.com/api-reference/inference/create-chat-completion.

Focused patch generation/admission: 37 passed. Full offline pytest: 605 passed,
101 PostgreSQL-dependent skips, one upstream AnyIO warning, exit 0. Repository
Ruff check/format, mypy packages/services and whitespace checks passed. No new
provider call was made: a live patch request must follow a genuine admitted
baseline, not fabricated source/baseline references. The gateway still needs
durable patch-model reservation, result retention and reducer/runner wiring;
M2 is not complete. Base ffcafe2fae11648f3390a647201da4df9a3cffd2; uncommitted.

### Explicit local credential loading and account limit discovery

The user explicitly authorized reading the workspace `.env`, including newly
added Tavily configuration. Credentials were read in memory only, without shell
evaluation or printing values. Presence of Nebius key, project identity and
Tavily key was confirmed; Tavily validity was not tested or claimed.
A single read-only Sandboxes whoami request returned HTTP 200. Sanitized limit
metadata is in `m2-env-effective-limits.json`. The previously missing process
configuration no longer blocks authenticated sandbox API access. This request
started no compute and made no remote mutation. CPU/memory/PID/filesystem and
retention evidence is not established by the returned account limit map.

### Network-disabled REST test request

Added `services/runner/rest_plan.py` to serialize a revalidated, fixed
TestCommandPlan into the documented REST spawn fields. Every constructed
request explicitly sets networking.enabled=false, shell=false, preserve_env=false,
non-root IDs, disposable execution, bounded output/layer bytes and the exact
application pytest argv/environment/cwd/timeout. It does not submit requests,
claim image UUID is an OCI digest, or confer execution authority. Six negative
and positive tests pass, including forged argv/environment/shell/timeouts/cwd.
Source: https://docs.tokenfactory.nebius.com/sandboxes/operate/security-and-data-handling.

Attempted the documented account-effective limit discovery without starting
compute. The current process lacked required sandbox credential/project
configuration, so no HTTP request was sent. Sanitized NOT_RUN evidence is
`m2-effective-limit-discovery.json`. This is a real external configuration
blocker for live acceptance; earlier successful sessions do not establish
credentials in this current process. No secret was printed or copied.

`uv run pytest -q`: 598 passed, 101 skipped (PostgreSQL not configured for this
ordinary offline invocation), one upstream warning, exit 0. Repository-wide
Ruff check/format and mypy passed. Last real PostgreSQL run remains 693 passed
in `m2-triage-controller-final-tests.xml`; the REST builder adds six pure tests
and does not alter persistence. M2 remains incomplete pending configured live
runner assessment, image binding, admission and baseline/patch dispatch.

### Controlled triage orchestration

Final full disposable-PostgreSQL rerun: 693 passed, zero skips, one upstream
AnyIO warning in 99.88 seconds, exit 0. Evidence:
`m2-triage-controller-final-tests.xml`. Repository-wide Ruff lint/format, mypy
packages/services and whitespace checks passed. Base commit:
`ffcafe2fae11648f3390a647201da4df9a3cffd2`; work remains local/uncommitted.

Added `services/orchestrator/repair_flow.py`. The controller revalidates the
provider destination/configuration, loads owner-scoped persisted context,
checks source identity and bounded encoded input size before reservation,
reserves a sticky model-call intent, makes one gateway request and atomically
retains the reproduction. Re-entering after successful retention validates and
returns the checkpoint without another request. Provider failure leaves the
uncertain intent and debit intact; restart cannot submit again.

Two real local PostgreSQL tests with an explicitly fake HTTP transport passed
in isolation (`m2-triage-controller-focused.xml`): success/retention/restart and
503/uncertain-intent/restart, with exactly one request and one debit in each.
The first full run had one unrelated statement-timeout failure in an existing
operation-journal test, preserved in `m2-triage-controller-tests.xml`; no timeout
or quality threshold was relaxed. A clean full rerun follows below.

Reviewed current official Nebius sandbox security/limits guidance and installed
SDK signature. The SDK 0.3.6 run interface lacks a networking argument, although
provider networking defaults to enabled. Required per-operation network denial
cannot be asserted for this adapter. Concrete execution and integration gaps
are recorded in `docs/m2-execution-blockers.md`. No paid provider call was made;
missing capability evidence must not be replaced by fabricated signatures.

### Sticky inference intent and atomic model-call debit

Validation: disposable local PostgreSQL full suite passed 691 tests, zero skips,
one upstream AnyIO warning, in 77.21 seconds, exit 0. Actual evidence:
`m2-triage-intent-tests.xml`. Tests cover restart duplicate denial with no second
debit, reservation-to-candidate handoff and event-failure rollback of budget,
checkpoint and intent. Migration downgrade/reapply passed. Repository-wide
Ruff check/format, mypy packages/services and whitespace validation passed.
Base commit `ffcafe2fae11648f3390a647201da4df9a3cffd2`; local uncommitted work.

Added migration `0011` and `ReproductionJournal.reserve_inference`. Under a live
owner-scoped lease and exact checkpoint version, TRIAGING with context and
source-manifest bindings may reserve one model-call unit. The run version,
budget, append-only uncertain intent and BUDGET_RESERVED event commit together.
The journal stores only attempt ID and a digest of immutable source/context
references, never prompt bodies, completions, reasoning or credentials.

An existing intent denies new submission as TRIAGE_SUBMISSION_UNCERTAIN even
after process restart. A lost response is deliberately not retried or refunded.
The reservation does not prove provider submission or return an execution
permission. Reproduction retention can use the new checkpoint version after a
successful separately controlled call. Model route/payload binding, priced
spend/time reservation and full orchestrator dispatch wiring remain pending.
No new cloud call was needed for this persistence-only change.

### Transactional reproduction retention

Final disposable local PostgreSQL acceptance: 689 passed, zero skips, one
upstream AnyIO warning, in 81.98 seconds (exit 0), including migration downgrade
and reapply. Evidence: `m2-reproduction-journal-final-tests.xml`. Repository-wide
`ruff check .`, `ruff format --check .`, `mypy packages services` and whitespace
validation passed. M2 stays partial pending model dispatch reconciliation,
runner admission and the fully correlated baseline/patch execution cycle.

Added migration `0010` and `ReproductionJournal` to retain bounded canonical
candidate bytes with a seven-day expiry. A live owner-scoped worker lease and
exact checkpoint version are required. Repository consent and proposal/source
bindings are revalidated before artifact insertion. The reference, transition
from TRIAGING to REPRODUCING, version increment and public metadata event commit
in the same transaction. Restart reads check expiry, owner, digest, canonical
bytes, current checkpoint identity and current consent. Runtime privileges are
SELECT/INSERT only for the new artifact table; no public source route was added.

The first real PostgreSQL run found an incorrect join to `runs.repository_id`;
repository identity lives in checkpoint JSON. Corrected the join to the existing
schema. Initial failure evidence remains in `m2-reproduction-journal-tests.xml`.
Tests exercise restart reads, wrong-owner denial, stale write denial, and event
failure rollback of both candidate bytes and checkpoint. No provider usage or
remote write was performed. Base commit:
`ffcafe2fae11648f3390a647201da4df9a3cffd2`; changes remain uncommitted.

### Immutable reproduction handoff

Added `services/orchestrator/reproduction.py`: canonical content-addressed
reproduction envelopes bind generated source to a run, repository, immutable
source commit, source manifest and exact context references. Binding/loading
revalidates current repository consent, approved test path, stage and
cancellation. A load additionally requires the checkpoint's exact reproduction
artifact reference and canonical retained bytes. It does not execute code,
write storage, mutate checkpoints or grant runner permission.

Eight offline tests cover round trip, unchanged input checkpoint, source/run/
context/manifest mutation, cancellation, wrong stage, tampering, wrong owner
and missing checkpoint reference. `uv run pytest -q`: 592 passed, 95 skipped,
one upstream AnyIO deprecation warning, exit 0. Repository-wide `ruff check .`,
`ruff format --check .`, and `mypy packages services` passed, exit 0. Base commit
`ffcafe2fae11648f3390a647201da4df9a3cffd2`; work remains uncommitted.

M2 is NOT complete: transactional proposal retention/checkpoint reduction,
durable model dispatch reconciliation, live runner capability evidence and
production assessor/signer provisioning, remote submission controller,
signed baseline reducer acceptance, model patch generation/application and
fully correlated candidate replay remain outstanding. The production
capability evidence/signing identity are external prerequisites; unit-test
keys and fabricated PASS claims must never enable this path. No additional
inference, sandbox call, remote write or cloud charge occurred in this slice.

### Runner preparation failure barrier

Inspection of the next reproduction-execution dependency found that upload,
clone and checkout exit codes were ignored. Tests could therefore run against
an absent or wrong workspace. Added a preparation barrier after each command:
nonzero exit raises sanitized `WORKSPACE_PREPARATION_FAILED` and no test
dispatch occurs. Three negative regression cases failed before the change
(clone failure, checkout failure, upload failure) and passed afterward.
The full offline suite passed: 584 passed, 95 skipped, one upstream AnyIO
warning (`uv run pytest -q`, exit 0). No provider request or cloud charge was
made. This barrier does not establish remote teardown or authorize execution.
M2 remains incomplete pending durable candidate storage and admitted remote
execution of a generated reproduction. Base commit:
`ffcafe2fae11648f3390a647201da4df9a3cffd2`; changes are uncommitted.

### Successful documented-profile validation (follow-up)

The user authorized a live test after documentation/reference review. One
no-retry request through `request_triage` used the revised request profile:
`reasoning_effort: none`, `max_completion_tokens: 1200`, JSON-object mode,
and `store: false`. Only a tiny synthetic Checkout class and incident were
provided; no repository or hidden oracle was supplied. Observed HTTP 200,
`finish_reason: stop`, 240 prompt tokens, 199 completion tokens, 439 total,
2.078 seconds, and no reasoning content. Local Pydantic and Python AST
validation accepted the proposal. Sanitized evidence:
`docs/evidence/m2-live-triage-reasoning-diagnostic.json`.

This proves the revised profile can return a completed schema-valid candidate
on the configured Nebius Nemotron route. It supports, but cannot retroactively
prove, the reasoning-budget explanation of earlier calls. NVIDIA's reference
implementation documents thinking controls; the Nebius endpoint documents the
top-level `reasoning_effort` option used here. The NVIDIA-specific chat-template
extension was not copied into the Nebius request without compatibility evidence.
References: https://docs.nvidia.com/nim/large-language-models/2.0.4/turbo/get-started-nemotron-3-super-120b-a12b.html
and https://docs.tokenfactory.nebius.com/api-reference/inference/create-chat-completion.

The generated source was kept only in memory, hashed and discarded; it was not
executed. Therefore this is generation/parse acceptance only, not an observed
behavioral baseline failure or runnable-candidate acceptance. M2 stays open.

Follow-up diagnosis: the request left reasoning effort at the provider default
while limiting generated output to 600 tokens. NVIDIA documents this as a
reasoning model, and Nebius documents a completion budget shared by visible and
reasoning tokens. This is a plausible cause of empty/incomplete output, not a
confirmed explanation of the historical calls: their sanitized evidence did not
retain exact finish reasons or token counts. Changing JSON formats alone did
not resolve those calls.

The adapter now explicitly requests `reasoning_effort: none` and
`max_completion_tokens: 1200`, removing the ambiguous `max_tokens` setting.
This is a bounded request-profile correction; provider/model support remains
unverified until a live call. No reasoning content is consumed as final output.
The payload regression failed before this change and passed afterward. Full
`uv run pytest -q`: 581 passed, 95 skipped, one upstream warning, exit 0.
Affected-file Ruff check, format check and mypy passed, exit 0. Build base:
`ffcafe2fae11648f3390a647201da4df9a3cffd2`; changes remain local/uncommitted.
No additional provider request or charge occurred during this diagnosis.
Sources: https://docs.tokenfactory.nebius.com/api-reference/inference/create-chat-completion
and https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b.

One newly authorized, no-retry inference request used the endpoint-compatible
`json_object` format, the bounded synthetic checkout-retry context, and
`store: false`. It returned after 4.421 seconds but did not have a completed
final choice, so the gateway rejected it as `TRIAGE_OUTPUT_INCOMPLETE`.
Sanitized metadata is stored in `m2-live-triage-json-object-attempt.json`; no
prompt, completion, candidate source, or credential was persisted. No sandbox
or repository operation occurred.

The triage gateway now separately reports a provider `finish_reason` of
`length` as `TRIAGE_OUTPUT_LIMIT`; other non-`stop` values remain
`TRIAGE_OUTPUT_INCOMPLETE`. The recorded attempt predated that diagnostic, so
its exact finish reason is intentionally unknown rather than inferred. Focused
gateway/parser/contract tests passed (22 tests), Ruff formatting/check and mypy
for the affected files passed. No further inference retry was made.

The complete local suite passed with 581 tests passed and 95 skipped. One
upstream AnyIO deprecation warning was reported.

## 2026-10-19 — Fixed checkout repair acceptance

Base `9696f99557615d531970c3ab0b7c0982011311d1`, local changes, no merge.
Read architecture, operating contracts, milestone acceptance and actual runner,
gateway and saved failed-attempt evidence. The gateway regression for complete
replacement source without a final newline failed before the correction
(`1 failed, 7 passed`) and passed afterward. Normalize the newline before
constructing deterministic hunks; retain strict broker path/size/schema checks.
Use distinct sanitized rejection codes instead of one undiagnosable error.

Authorized command `uv run python -m scripts.e2e_checkout --live --env-file .env
--resume-baseline` exited 0, `FIXTURE_REPAIR_PASSED`. One inference and two small
disposable VM submissions, no retries, baseline/test reused from saved evidence.
Baseline exit 1; Git-applied inferred candidate and fresh replay exit 0 with
matching source/test and candidate tree identities. Artifact digest and three
distinct operation IDs checked offline, exit 0. Earlier failures preserved.
See `m2-checkout-acceptance.md` and `evidence/m2-checkout-e2e/report.json`.

Added safe path/command denial audits before network submission and tests;
guarded resume against completed/partially executed candidates; fixed Windows
evidence LF serialization. Offline suite: 613 passed, 103 PostgreSQL skipped,
one upstream AnyIO deprecation warning, exit 0. Two later CLI resume regression
cases passed. Ruff check, format (136 files), mypy (72 core/script files) and
git diff --check exited 0. Checkout evidence scan against configured secret
values passed across six files without printing values. No new dependency or
UI change; dependency vulnerability/UI checks were not rerun.

The standalone fixed-fixture M2 exit demonstration passes. General repository
runner admission remains blocked on real capability/signing evidence; durable
worker graph integration is still implementation work. No VERIFIED verdict,
production readiness, hidden-oracle or PR acceptance is claimed.

Final local PostgreSQL acceptance: the existing `scripts.test_postgres` harness
created task-owned random database/roles, applied migrations, ran the whole suite
and cleaned up. `m2-checkout-postgres-tests.xml`: **718 passed, zero skipped**, one
upstream warning, 90.66 seconds, exit 0. Administrator credential supplied only
in memory; no database or role outside this disposable test scope was altered.

## 2026-10-19 — M2 acceptance integrity and durable baseline reduction

Read AGENTS.md, Architecture.md, instructions.md, milestones.md, todo.md and the
runner/receipt/journal/controller implementations and tests. Base remains
9696f99557615d531970c3ab0b7c0982011311d1 plus prior local work. No external writes,
inference or sandbox submissions were made in this attempt. Existing real live
fixture evidence was reused rather than spending to reproduce the same result.

Implemented `BaselineJournal.accept`: fence owner/live lease/version, require
matching command reservation and retained receipt, check current consent and
source/test/run bindings, reauthenticate the actual report under configured
trusted keys, and atomically advance PATCHING or close invalid reproduction as
INCONCLUSIVE. Transition and audit rollback together. The trusted continuation
invokes the existing one-shot patch controller only after acceptance and skips
acceptance on a resumed later-stage checkpoint. This does not add a production
signing identity or bypass the runner capability gate.

Root-cause correction found during review: receipt inspection had hardcoded
assertion exit 1, preventing authentic syntax/setup exit 2 from being retained
and classified. Both new exit 0/2 tests failed before the correction. Signature
authentication now precedes inspection of the actual exit; rejected outcomes
can close INCONCLUSIVE, while direct assertion receipt verification still
requires exit 1. No signature, scope, freshness or correlation check was removed.

Added an offline M2 fixture consistency evaluator and CLI. It checks original
identities, fixed patch scope, patch/test digests, separate operation IDs,
terminal outcomes and candidate/replay tree equality. It returns
M2_FIXTURE_ACCEPTED with verification/execution authority explicitly false.
Digest/source/tree/image/operation/outcome tampering and missing tests are
negative cases. Invalid generated syntax returns INCONCLUSIVE before dispatch.

Pinned Git source-file LF handling and disabled evidence byte rewriting so
source and evidence checksums survive Windows/Linux checkout. Existing captured
artifact bytes were not rewritten. Targeted configured-credential-value scan
passed without outputting credential values. Dependency and UI code are
unchanged; full vulnerability/UI/readiness checks were not run or claimed.

The fixed-fixture M2 exit gate passes from real retained evidence. Full
production M2 remains open: a real assessed runner/collector and trusted key
configuration, generic dispatch/polling integration, and complete model
spend/time/request binding remain required. Production VERIFIED, hidden-oracle
acceptance, approval, PR publication and deployment are not asserted.

Final validation after the exit-code correction: **746 passed, zero skipped**,
one upstream AnyIO deprecation warning, 92.23s, exit 0. Invoked the existing
`scripts.test_postgres` harness with evidence-name
`m2-closure-exit-handling-postgres.xml`; supplied the previously authorized local
PostgreSQL credential in memory. Its task-owned disposable DB/roles were removed.
Ruff check/format (140 files), strict mypy (74 modules), Git diff checks, offline
acceptance and committed artifact-byte checksum comparison all passed, exit 0.
Captured evidence and fixture-byte Git attributes preserve checksums across
platforms; CRLF evidence is retained verbatim and reviewed with cr-at-eol handling.

Local commits: `15d131e` records baseline acceptance/continuation and real exit
classification; `7cf4349` records fixture acceptance/integrity and real saved
artifacts. Branch `codex/m2-checkout-closure`; no push or merge. The pre-existing
untracked `.env.example` was not modified or staged. Three fixed-fixture tracker
items are now checked against committed executable evidence. Broader production
items remain open rather than being silently redefined as complete.

## 2026-10-19 — Request-bound inference budget reservations

Base 165ea15; implementation commit 3c8e2b3 on codex/m2-checkout-closure.
Read operating contracts, architecture/playbook/milestones/tracker and active
runner, budget, triage, patch and persistence contracts. General runner admission
still lacks external assessed capability/signing evidence. Closed the next local
prerequisite: the production controllers previously debited only model-call
count, without binding the request, configured timeout or declared estimate.

Added closed RequestIdentity/ModelCostPolicy/ModelDispatch contracts. Gateways
validate configuration, serialize once and expose a safe pre-submission identity;
the same bytes are sent as application/json. Controllers require an explicit
model/endpoint-matching operator policy for a new call, reserve all three
resources and metadata atomically in existing append-only intents, then recheck
the version before HTTP submission. Migration 0013 adds bounded nullable JSONB
metadata; historical unpriced intents cannot be upgraded into another call.
Provider/output faults retain sticky intent and do not refund or retry.

Exhaustion commits INCONCLUSIVE/BUDGET_EXHAUSTED and closes the lease without
intent insertion or partial resource debit. Audit failure rolls the transaction
back. Tests cover both triage and patch reservations, three exhausted resources,
concurrent requests, restart/duplicate denial and policy mismatch. The mocked
HTTP handler reads committed metadata and checks SHA256 against actual wire
bytes; no fake transport result is presented as live provider acceptance.

The first two pre-submission tests failed before implementation. Review also
reproduced an oversized-response bug: triage buffered the full stream before
checking its size. A failing streaming regression demonstrated continued reading
past the limit; bounded incremental collection now stops immediately.

Actual validation: focused DB/model dispatch run 17 passed, exit 0; initial
full disposable-PostgreSQL suite 764 passed, exit 0. After streaming and missing
policy regressions, final full run **765 passed, zero skipped**, one upstream
AnyIO deprecation warning, 100.07 seconds, exit 0. Evidence:
`evidence/m2-model-budget-final-postgres.xml`. The existing scripts.test_postgres
harness used an in-memory local admin credential and removed its random
task-owned DB/roles. Ruff check/format (145 files), strict mypy (77 core/script
modules), Git diff checks and a targeted configured-credential-value scan
(12 changed-module/evidence files) passed. Dependencies/UI were unchanged;
vulnerability/UI/readiness gates were not rerun or claimed.

No inference, sandbox submission, remote write, push, merge or deployment was
performed. Existing live fixture evidence and pre-existing untracked files were
preserved. Estimates are operator declarations rather than verified pricing;
HTTPX timeout reservations are not absolute wall-clock enforcement. Production
runner admission/dispatch, measured elapsed-time/usage reconciliation and full
worker integration remain open. See inference-reservations.md for the internal
caller contract and migration/rollback limits.

## 2026-10-19 — Baseline command timeout reservation

Base 672fc4f; implementation commit 6f18eb2 on codex/m2-checkout-closure.
Inspected operating documents, actual baseline persistence, plans, budgets and
related tests. Found that baseline dispatch reserved one command but ignored
the immutable 60-second plan timeout. Three PostgreSQL regressions failed
before the fix, including acceptance with insufficient remaining seconds.

Baseline reservation now debits command count and the complete planned timeout
atomically through the shared reservation transaction. Migration 0014 adds
bounded reserved_seconds metadata; historical records remain NULL rather than
claiming earlier time accounting. Exhaustion durably closes INCONCLUSIVE and
releases the lease without partial debits or an intent. Audit failure rolls
back; existing intent reconciliation neither resubmits nor debits again.
Added exhaustion, exact-boundary, audit-rollback and concurrent accounting
assertions. This metadata grants no execution authority.

Actual validation: focused PostgreSQL tests **29 passed**, exit 0. Final full
scripts.test_postgres disposable-database harness: **769 passed, zero skips**,
one existing upstream AnyIO warning, 105.71 seconds, exit 0, captured in
evidence/m2-sandbox-time-postgres.xml. The harness migrated through 0014,
exercised downgrade/reapply and removed its task-owned DB/roles. Local admin
credentials were supplied only in memory. The three-failure red XML was
archived losslessly as evidence/m2-sandbox-time-red.xml.gz; decompressed bytes
were checked against the original capture. Focused evidence is retained too.
Ruff check, Ruff format --check (146 files), strict mypy (77 files), Git diff
checks and targeted configured-secret-value scanning of seven changed files
passed. Dependencies and UI were unchanged; no broader readiness claim.

No inference, sandbox submission, remote write, push, merge or deployment was
performed. Pre-existing untracked files were preserved. Fixed-fixture M2
acceptance remains distinct from full production M2: trusted assessed runner
capabilities/signing, general admission-gated dispatch, polling-worker
integration and actual elapsed-time/usage reconciliation remain outstanding.
The next critical-path prerequisite is the assessed runner/collector setup;
its missing evidence cannot be replaced by synthetic test signatures.

## 2026-10-19 — Durable known-operation read-only recovery

Base dbda202; implementation commit b4c9b9b on codex/m2-checkout-closure.
Read AGENTS, Architecture, instructions, milestones, tracker, active admission,
operation and baseline contracts, worker and tests; inspected recent commits.
The standalone fixed-fixture M2 exit is already evidenced. Production execution
is still blocked on real assessed runner controls and collector signing.
Implemented the next independent integration prerequisite: reconnecting durable
known-operation history to the existing bounded REST status reader.

The explicit controller validates owner, active lease, action/run identity and
transport provenance before HTTP. It reads only the persisted operation UUID,
checks provider/image identities, rechecks the fence after I/O and appends
terminal metadata through the existing journal transaction. It performs no
POST/DELETE, automatic retry, cancellation, refund or stage promotion. Pending
operations remain pending; restart after terminal persistence performs no GET.
No raw provider output appears in the returned metadata. Custom HTTP transports
stay TEST_TRANSPORT; real adapter metadata is explicitly unverified. A service
SUCCESS state cannot become a signed passing test report.

Official Nebius execution-lifecycle and choose-an-interface documentation were
checked online before implementation; URLs and semantics are recorded in
operation-recovery.md. No credentials were sent during that research.
Initial red evidence records six missing-module failures, demonstrating the
absent feature rather than an existing runtime regression. Initial focused
tests also exposed a test-fixture error: a repeated operation UUID correctly
violated the journal's uniqueness constraint. Unique per-case IDs fixed the
fixture; the production uniqueness constraint was preserved.

Focused PostgreSQL/synthetic-HTTP checks: 15 passed, exit 0. Added four further
lease-loss/version-change/database-rollback/provenance cases. Final full command
used scripts.test_postgres with --evidence-name m2-operation-recovery-postgres.xml,
supplying the authorized local PostgreSQL password only in memory: **788 passed,
zero skipped**, one existing upstream AnyIO warning, 148.40 seconds, exit 0.
Task-owned disposable database/roles were removed by the harness. Evidence:
evidence/m2-operation-recovery-postgres.xml; focused XML and lossless gzip red
capture retained separately. Ruff lint/format (148 files), strict mypy
(78 files), staged diff checks and targeted credential-value scanning (six
changed files plus final suite XML) passed. Offline scripts.check_checkout
still returns M2_FIXTURE_ACCEPTED, exit 0, with execution/verification authority
false. Dependencies/UI were unchanged; full deployment-readiness is not claimed.

No cloud API request, inference, remote write, push, merge or deployment occurred.
Existing untracked .env.example and m2-baseline-checkpoint-red.xml were preserved.
M2's fixture contingency passes; broad production M2 remains incomplete.
Next: obtain independently assessed runner configuration/control evidence and
trusted collector signing, then wire admission-gated submission, signed result
collection, candidate/replay and the polling worker. This controller supplies
recovery plumbing only and cannot manufacture those external prerequisites.

## 2026-10-19 — Collection-to-triage worker integration

Base b460774; implementation commit 7c43858 on codex/m2-checkout-closure.
Reviewed the actual worker, graph reducer, job lease implementation, reproduction
journal, request-bound controller and related contracts/tests. Found a local
integration gap: collection completed its lease at TRIAGING, so the existing
polling path could not hand work to the inference controller. This was
unfinished code, not an external provider blocker.

Added explicit queue_triage opt-in through collection, tick/serve and the
run_worker --queue-triage flag. Context, provenance and checkpoint now commit
with either the unchanged default done job or an explicitly queued triage job;
the old fence is cleared and the next stage's recovery count starts at zero.
Default claim excludes TRIAGING; dedicated tick_triage claims only TRIAGING.
It invokes the existing owner-scoped one-shot controller with an explicit
operator cost policy. Successful proposal/checkpoint retention and triage-job
completion share the same database transaction. No generated test runs locally.

Tests cover real collection/provenance to queued handoff, default collection
worker isolation, restarted inference-worker retention, exactly one synthetic
HTTP request, completion/idleness, uncertainty after lease expiry, and rollback
of the context/queue handoff on audit failure. Initial red run had three absent
entry-point/argument failures. The first implementation run additionally
rejected a test fixture missing source provenance; the fixture was corrected
to carry an explicitly TEST_TRANSPORT manifest, preserving the production gate.

Focused harness run: 3 passed, exit 0. Full scripts.test_postgres with
--evidence-name m2-triage-worker-postgres.xml: **791 passed, zero skipped**,
one existing upstream AnyIO deprecation warning, 138.43 seconds, exit 0.
Authorized local PostgreSQL credentials were supplied only in memory; the
harness removed its task-owned DB/roles. Red XML is archived losslessly in
evidence/m2-triage-worker-red.xml.gz; focused and final XML are retained.
Ruff check, Ruff format --check (149 files), strict mypy (78 files), staged
diff checks and configured credential-value scanning of 12 changed files passed.
Default run_worker printed NOT_RUN without --run; check_checkout returned
M2_FIXTURE_ACCEPTED with both execution and verification authority false.

No live inference, sandbox submission, remote write, push, merge or deployment
occurred. Dependencies/UI were unchanged; broader readiness was not asserted.
Pre-existing untracked files were preserved. The fixture M2 exit remains
accepted; production M2, M3 verification/UI, M4 approval/PR/evaluation and M5
deployment/submission remain open. Remaining M2 work includes a genuinely
assessed runner, independently signed report collection, admitted baseline and
candidate submission, and the downstream durable graph. The triage handoff
does not claim to supply those controls or complete all phases.

## 2026-10-19 — Restart-safe signed baseline report delivery

Base f70d96f; implementation commit 6ee1a71 on codex/m2-checkout-closure.
Read operating documents and active journal, receipt, reducer, recovery and
worker contracts; inspected implementation and recent changes. The next local
gap was that signed receipts persisted but their actual JUnit bytes did not,
preventing restart-safe baseline acceptance without an external report resend.

Migration 0015 adds bounded nullable report_payload to baseline_receipts.
New receipt/report/audit writes share one transaction; existing runtime grants
are unchanged. Private owner-scoped report reads validate expiry, canonical
receipt integrity, execution identity, size and signed report SHA256. Legacy
NULL reports stay unavailable rather than being fabricated. Producers must
redact before signing; no raw report enters checkpoints, events or public routes.

receive_baseline and accept_retained_baseline connect delivery/recovery to the
existing reducer without inference or execution. Fresh acceptance still checks
the live owner/lease/version, current consent, reserved action, trusted key and
fresh signature. Acceptance audit failure leaves a recoverable retained report
while rolling back the checkpoint. Concurrent delivery reconciles committed
content; conflicting redelivery and unrelated stages fail closed. Reconciliation
does not refresh evidence or grant a new action. No signing service/key or new
runtime capability claim is introduced by this receiver.

Observed tests: initial red 9 missing-feature failures; focused delivery checks
10 passed after adding the audit rollback test. One full run exposed an incorrect
static-method test hook (1 failed, 800 passed); corrected the hook. Another full
run exposed a real concurrency race (1 failed, 800 passed): completion between
two nontransactional checkpoint reads raised STALE_VERSION outside reconciliation.
Removed the redundant read; the transactional reducer remains the authority for
lease/version checks. Added a deterministic competing-completion regression.
Both failure captures and original red XML are retained as lossless gzip files.

Final scripts.test_postgres --evidence-name m2-baseline-delivery-postgres.xml:
**802 passed, zero skipped**, one existing upstream AnyIO warning, 132.64 seconds,
exit 0. Local PostgreSQL credentials were supplied only in memory; the harness
removed its random task-owned DB/roles. Migration, downgrade/reapply, existing
unit/contract and negative-policy cases passed in this run. Ruff check/format
(152 files), strict mypy (79 files), staged diff checks and configured-secret
value scanning of 12 changed files plus final XML passed. Offline check_checkout
still returns M2_FIXTURE_ACCEPTED, exit 0, with no execution/verification authority.

No cloud calls, remote writes, push, merge or deployment occurred. Dependencies
and UI were unchanged, and no broad readiness claim is made. Pre-existing
untracked files were preserved. The fixed-fixture M2 exit passes; production M2
still requires a genuinely assessed runner, independently observed collector
facts and deployed signing identity, followed by admitted baseline/candidate and
worker integration. This closes report delivery/recovery plumbing, not those
external runtime facts. Details and rollback/privacy limits: baseline-delivery.md.

## 2026-10-19 — Close the written fixed-fixture M2 exit

Base cad94a3. Re-read milestones.md, the actual fixture verifier and CLI/policy
tests, acceptance documentation and tracker rather than inferring status from
checkboxes. The original M2 exit is specifically the checkout fixture failure
and inferred candidate execution, with source/tree identity, audited forbidden
paths/commands and invalid-syntax rejection. These criteria are already met;
the prior broad production-M2 wording was obscuring the completed milestone
exit. No original acceptance condition or mandatory production gate changed.

Actual commands: uv run python -m scripts.check_checkout returned
M2_FIXTURE_ACCEPTED, exit 0. uv run pytest -q
tests/unit/test_checkout_acceptance.py tests/unit/test_checkout_benchmark.py
tests/unit/test_checkout_cli.py --junitxml=docs/evidence/m2-final-exit-tests.xml
passed **18 tests, zero skips**, 0.30 seconds, exit 0. Ruff check, Ruff format
--check (152 files), strict mypy (79 files) and staged diff checks passed.
Compared the three live artifact bytes against both m2-closure-acceptance.json
and git show HEAD:path; all checksums and bytes match. Wrote m2-final-exit.json
mapping each written criterion to its actual evidence and provenance. The prior
802-test PostgreSQL result belongs to the unchanged implementation and was not
rerun for this documentation-only closure.

No cloud calls, new live inference, remote writes, deployment or merge occurred.
M2_FIXED_FIXTURE_EXIT_COMPLETE is a scoped milestone result, not VERIFIED or
general-repository execution authority. The ledger explicitly records
production_m2_complete=false. Production runner/signing, full graph and measured
usage tasks stay open; their checkboxes were not promoted. Proceed to M3 trust
and UI within the existing fixed-fixture contingency, showing actual statuses
and limitations. Existing untracked user files remain untouched.

## 2026-10-20 — M3 authenticated gates and private evidence API

Resumed from f1b7acf with unfinished evidence-store work present in the index.
Re-read operating contracts, architecture, instructions, milestones and tracker;
inspected actual verifier, persistence and API behavior. Preserved the existing
staged .env.example and m2-baseline-checkpoint-red.xml without including them in
these commits. Implementation/evidence commit d62d607 completes the resumed
storage/API slice; the earlier authenticated-gate increment is f1b7acf.

Authenticated-gate review found the old five-gate unsigned evaluator could label
caller-created evidence VERIFIED and omitted EXISTING_SUITE_PASS and
SECURITY_GATE_PASS. Its positive preview is now GATES_SATISFIED without authority.
The separate authenticated boundary checks an explicit trusted Ed25519 registry,
dedicated purpose/policy, freshness, expected run/version/source/patch/test/image/
configuration, actual manifest bytes, patch/test presence and distinct baseline,
candidate and replay operation identities. Optional static-analysis failure cannot
pass. No caller can waive mandatory gates. Tests use ephemeral synthetic producer
keys; no live producer or operational verification is claimed. Initial red:
24 failed, 3 passed. Focused checks: 34 passed before the configuration-mismatch
addition. Full PostgreSQL run: 830 passed, zero skips, 136.08 seconds, exit 0,
captured in m3-gates-postgres.xml. Ruff/type checks passed with that increment.

Migration 0016 and EvidenceStore now retain canonical bounded private bundles
with source/run identities, manifest and exact bytes. A live owner/lease/version,
VERIFYING stage and current consent gate retention. Bundle/ref/version/event
commit together; duplicate concurrent requests reconcile. Owner-scoped reads
check expiry, hashes and complete decoded content, including arbitrary binary
round trips. Runtime role privileges are SELECT/INSERT only. No artifact bytes
are written to public run events. Default lifetime is fixed 24-hour read expiry.
This does not implement physical purge or deployed encryption/backup retention.

The first focused run exposed four failures from the database event allowlist:
EVIDENCE_RETAINED had not been migrated. Added that event explicitly to the
closed vocabulary. Partial downgrade keeps the additive vocabulary valid for
existing immutable audit records while dropping the evidence table. Full
downgrade/reapply passes in the disposable harness. Initial absent-module red
had eight failures; the corrected storage checks passed eight tests.

Added authenticated loopback-only GET manifest and attachment endpoints. Metadata
contains names/hashes/sizes rather than bytes/paths. Downloads use octet-stream,
attachment, nosniff and no-store. Owner/auth/expiry/integrity checks apply to both
routes; unknown artifacts return 404. Four API tests failed before the routes
existed (two negative 404 cases already passed). The final focused run passed
15 storage/API cases. Expired content cannot be resubmitted to refresh lifetime;
audit failure rolls back, and the runtime cannot UPDATE/DELETE retained rows.

Final command: scripts.test_postgres --evidence-name
m3-evidence-store-postgres.xml, with authorized local PostgreSQL credentials
supplied only in memory: **845 passed, zero skipped**, one existing upstream
AnyIO warning, 164.43 seconds, exit 0. The harness removed its task-owned DB/roles.
Ruff check and format --check (157 files), strict mypy (81 files), scoped staged
diff checks and configured-secret-value scanning of ten changed files/evidence
artifacts passed. Red XML captures were archived losslessly. No dependencies
changed and no frontend was implemented; broader readiness is not asserted.

check_checkout still returns M2_FIXTURE_ACCEPTED, exit 0, with no execution or
verification authority. No model, sandbox, remote-write, push, merge or deployment
call occurred. M3 stays partial: producer/signing deployment, raw runtime proof,
durable authenticated verdict reduction, physical retention and UI acceptance
remain open. The next integration must reverify under the current checkpoint and
scope; neither stored hashes nor caller-supplied verdict objects may open a
privileged stage. See verification-gates.md and evidence-storage.md for contracts.

### 2026-10-20 — Checkpoint-bound gate evaluation

Added EvidenceStore.evaluate: reload current owner-scoped checkpoint, validate
worker fence/version/stage/consent, decode the current retained manifest and
actual bytes, derive run/source/latest-patch/reproduction identities from state,
and authenticate gates using operator image/configuration and database time.
No external request, state transition, publication authority or API write route
is introduced. Baseline authenticity remains a producer responsibility; this
adapter requires its reference but does not reinterpret raw runner reports.

Thirteen tiny synthetic PostgreSQL cases passed in 15.13 seconds, exit 0, with
one upstream AnyIO warning (m3-evidence-evaluation-focused.xml). Negative cases
assert specific denial codes, preventing an unrelated early denial from masking
an untested branch. Successful and denied evaluations leave state and events
unchanged. Initial test harness mistakes (Store.get name and absent synthetic
baseline ref) were corrected before recording acceptance. Ruff lint/format
(158 files), strict mypy (81 files), scoped secret-value scanning and diff checks
passed. No dependencies changed. Full-suite evidence is recorded separately
after completion. Existing staged user files were preserved.

Full disposable PostgreSQL acceptance: **858 passed, zero skips**, one upstream
warning, 171.81 seconds, exit 0 (m3-evidence-evaluation-postgres.xml), based on
parent c59e535 plus this change. The in-memory test invocation allowed 300 seconds
instead of the harness default 180; no test selection or assertions were removed.
The harness removed its task-owned database and roles. Offline check_checkout
still reports M2_FIXTURE_ACCEPTED with no execution/verification authority.
M3 remains partial: durable verdict reduction, assessed producer deployment and
live proof, physical retention, authentic research integration and UI remain open.

### 2026-10-20 — Durable authenticated verdict reduction and private API

Added migration 0017 and VerificationJournal. The reducer shares the existing
checkpoint-bound evaluator inside its committing transaction: actual retained
bytes, live lease, exact version, consent, context, signature and gate policy are
checked again. Signed envelope plus computed verdict, hashed verification_ref,
stage/version, STAGE_CHANGED event and job completion commit atomically. A final
lease check rolls all writes back if expiry occurs during validation.

Concurrent exact redelivery reconciles the historical committed state without
another transition or retention extension. Conflicting delivery/configuration,
changed checkpoint, expiry and corruption fail closed. The generic reducer still
cannot manufacture VERIFIED, and no verdict-upload or PR authority was added.
GET /api/v1/runs/{run_id}/verification exposes an owner-authenticated, no-store
verdict and bounded gate matrix without signatures/raw reports. Runtime verdict
records are SELECT/INSERT only. Read expiry is not physical erasure.

Focused disposable PostgreSQL acceptance: **44 passed**, 845 deselected, zero
skips, one upstream AnyIO warning, 43.86 seconds, exit 0
(m3-verdict-focused.xml). It includes the existing 13 evaluation cases and 31
journal/API cases: three outcomes, concurrent restart/idempotency, specific
denials, audit rollback, expiry during commit, private reads, safe API and denied
runtime mutation/deletion. Red captures for absent reducer/API are archived
losslessly as m3-verdict-red.xml.gz and m3-verdict-api-red.xml.gz.

Ruff lint/format (161 files), strict mypy (82 files), diff checks, scoped configured
credential-value scans and offline M2_FIXTURE_ACCEPTED passed. Parent build is
942e0c4 plus this change; full-suite result follows after completion. No dependency
or frontend changes, production migration, remote write, inference, sandbox,
push, merge or deployment occurred. Tests use tiny synthetic artifacts and
ephemeral authority; no live verified repair or producer deployment is claimed.

Full acceptance: `scripts.test_postgres --evidence-name m3-verdict-postgres.xml`
with the authorized local password supplied only in memory and the subprocess
ceiling increased from 180 to 300 seconds: **889 passed, zero skips**, one upstream
AnyIO warning, 192.93 seconds, exit 0. No test filters or assertions were removed.
The harness removed its task-owned database/roles. Final Ruff lint/format, strict
mypy and scoped secret/diff checks passed. M2 fixture acceptance remains intact.
M3 remains partial pending actual producer/runner integration, physical retention,
authentic research integration, UI and live end-to-end acceptance.

### 2026-10-20/21 — Workbench, research path and approved live lookup

Snapshot 65bccad retains the Next.js local workbench, owner-scoped recent-run and
patch reads, explicit loopback API startup and disposable real-API/browser harness.
It also retains migration 0018, sticky research intents, atomic query/time/declared
USD reservations, private hashed citation reports/events and the triage context
integration. Research text is named untrusted_documentation and has no tool
authority. Query vocabulary remains closed; no incident/code is submitted to Tavily.

Retained backend report m3-workbench-research-postgres.xml records 905 tests with
zero failures/errors/skips, suite time 191.980s, from the full disposable PostgreSQL
run using a 300-second harness-child ceiling. Focused evidence records 16 passes
in 12.93s. These are prior observed results, not a new full-suite run on October 21.
The current changes affect frontend CSP/tests and retained evidence/documentation;
the backend implementation has not changed from that tested snapshot.

After the user explicitly approved one search, scripts.accept_research --live ran
once as a child of the disposable DB harness. Only TAVILY_API_KEY and feature
enablement were loaded into memory; credentials were not printed or written.
The actual broker returned SUCCESS / REMOTE_SERVICE, two public sources and one
reported usage credit. Canonical record: m3-live-research.json, SHA256
0272b42fec7ac4d270e256ae0fc6d5c56f2e5efd67ea7563e7f3766594202d0a.
The record was read back from PostgreSQL before the harness removed its DB/roles.
The 50,000-microusd budget reservation is declared, not confirmed billing. Returned
ranking included an Indonesian 3.12 index and a current 3.15 reference; no
version-specific correctness is claimed, and no retry occurred.

Final browser command: scripts.test_postgres --web, administrator secret provided
only in memory. Seven tests passed, zero skipped, 20.5s, exit 0. One journey uses
the real API/PostgreSQL to create, inspect, cancel and reload a tiny incident.
Three UI contracts use synthetic provider data; one renders archived live research
with mocked surrounding HTTP and a fixed checksum. Two pure bridge/parser checks
cover cross-origin/path denial and cursor semantics. No source code executes.
Screenshots were visually reviewed at desktop and 390px mobile; overflow checks,
missing-gate display, inert diff/log rendering and interrupted replay passed.
The timeline archive check adds no provider requests.

UI lint/typecheck/production build passed. Production CSP no longer permits
unsafe-eval or development WebSockets; project-local build roots avoid the unrelated
parent-lockfile warning. npm audit reports zero known vulnerabilities. Final Ruff
check/format (169 files), strict mypy (86 files), scoped credential checks and Git
diff checks passed. M2_FIXTURE_ACCEPTED remains intact. Backend tests were not
repeated merely to restate unchanged evidence. No dependency versions changed in
this update, and no public-readiness or WCAG audit claim is made.

The local UI/research/reconnect rows are accepted. M3 itself remains open because
live seven-gate verification needs genuine runner/image/configuration evidence
and a trusted deterministic producer/signing deployment. Fixed-fixture reports
and test keys are not substituted for those facts. Physical purge, storage
deployment guarantees and public deployment remain separate. This update made
no inference/sandbox call, repository write, merge or deployment. See m3-acceptance.md.


## 2026-10-22 — Explicit JUnit result-image export

Base commit f5f1c54. Implemented PYTEST_FILES_SAVED_JUNIT_V1 as an opt-in
closed command template, with distinct canonical plan identity and unchanged
pytest argv/network/shell/environment bounds. Existing disposable plans remain
unchanged. Added owned terminal-result-image JUnit collection with private exact
bytes/SHA256, 64 KB HTTP bound, fixed report path and explicit unavailable/error
outcomes. Failed test operations may export reports; unrelated, running/cancelled,
source-image, empty/oversized/redirect/missing-file cases fail closed. Candidate
and clean replay retention policies must match before reservation/inspection.
See docs/prepared-nebius-backend.md for provider sources and retention impact.

Observed commands (all exit 0):
- `uv run pytest tests/contract/test_prepared_backend.py tests/unit/test_rest_plan.py tests/unit/test_command_policy.py tests/unit/test_replay_pair.py -q --junitxml=docs/evidence/m3-junit-export-contract.xml`: 69 passed, zero skips.
- `scripts.test_postgres --evidence-name m3-junit-export-postgres.xml`, invoked
  through the documented Python wrapper with the outer suite ceiling 420s:
  **1041 passed**, zero failures/errors/skips, **300.91s**. One existing upstream
  Starlette/AnyIO deprecation warning. Task-owned DB/roles cleaned by harness.
- `uv run ruff check .`, `uv run ruff format --check .`: pass, 186 files.
- `uv run mypy`: pass, 93 source files.
- `git diff --check` and scoped credential-value-pattern scan: pass.
- `uv run python -m scripts.check_checkout`: M2_FIXTURE_ACCEPTED.

The new pair-policy negative was observed failing at the wrong (later receipt)
binding guard before the earlier pair/reservation policy checks were added; it
then passed. HTTP cases are tiny synthetic transport exchanges, not remote
execution. No repository code ran on the host; no paid inference/sandbox/Tavily
call, GitHub write, deploy or merge occurred. Authentication is unchanged.
No dependency/UI changes required repeating their previous audits/browser suite.

M3 remains NOT CLOSED: general materialization, bounded runtime orchestration,
trusted receipt producer and assessed live seven-gate acceptance remain open.
Saving an image retains the entire filesystem and does not establish deletion.
The new collector does not sign reports or establish final-tree identity; those
require the independently configured producer. Next implementation is connecting
collection to that producer and owned operation recovery/cleanup, followed by
small explicitly authorized live acceptance. No production readiness is claimed.


## 2026-10-22 — Durable recovery and repository-scoped lesson recall

Base f1a25d5. Preserved existing PostgreSQL checkpoint, lease and sticky-intent
recovery. Added bounded read-through RunMemory over accepted verification records:
same owner/repository/policy, currently consented source commits, canonical/hash
and checkpoint binding, verification/evidence expiry and cancellation checks.
Cards retain source/patch/manifest/verdict identities and failed/missing gates;
there is no free-form memory writer, extra model call or new vector/database
service. Recall is advisory and cannot authorize actions or verification.

Connected recall to run_triage within existing 8-entry/4096-byte request bounds,
without displacing current evidence. The exact budgeted request hash includes
selected lesson bytes. Completed triage still reconciles without another request.
Moved the unchanged closed GateName vocabulary into contracts while preserving
its existing verifier import. Auth/UI and provider keys were unchanged.

Observed evidence (all final checks exit 0):
- Focused unit/gateway run: **14 passed**, zero skips.
- Real PostgreSQL + synthetic HTTP memory-to-triage/restart handoff:
  **1 passed**, 1056 deselected, m3-memory-handoff.xml.
- Final full disposable-PostgreSQL suite: **1057 passed**, zero failures/errors/
  skips, **303.33s**, m3-memory-postgres.xml. One existing upstream Starlette/AnyIO
  deprecation warning. The harness cleaned its task-owned DB/roles.
- `uv run ruff check .`, `uv run ruff format --check .`: pass (191 files).
- `uv run mypy`: pass (96 source files). Diff and scoped credential-value checks
  pass. `scripts.check_checkout`: M2_FIXTURE_ACCEPTED.

Initial missing-module RED and intermediate focused failure reports are retained
as m3-memory-red.xml.gz and m3-memory-focused.xml.gz. An overly strong grant row
lock initially required privileges unavailable to the runtime role; recall now
uses the existing read-only grant permission, without broadening grants. New
handoff-test setup was corrected to use triage_only and a source-manifest ref;
no production gate was weakened. The full suite covers the corrected test.

No paid inference, sandbox, Tavily call, remote repository write, merge or deploy
occurred. Gate signatures/provider responses in memory tests are synthetic; this
is not live memory accuracy or live VERIFIED acceptance. No dependency/UI changes
required repeating their earlier audits/browser checks. Details: docs/memory.md.

Limitations: structured outcome recall, not semantic/root-cause learning;
retention remains tied to source evidence (currently 24h default); future
approval/publication stage recall and a UI view remain unimplemented. M3 is not
closed by memory. Separate requested harness specifications are still missing.


## 2026-10-22 — Source-informed bounded collection/triage harness

Base d5cf18e. Reviewed official Anthropic long-running-agent guidance and its
reference README, plus LangChain's harness anatomy and harness-engineering posts.
Sources and decisions are linked in docs/agent-harness.md. Implemented an original
fixed-stage scheduler using existing workers, typed state, memory, leases,
request reservations and journals; no framework/model/provider was replaced.

The harness alternates collection/triage fairly, queues handoff atomically, caps
steps/waits and stops cooperatively. Exceptions propagate without blind retry.
The existing CLI adds --harness and explicitly gated live inference with a
required declared per-call cost estimate. Default invocation performs no I/O;
inference-off never reads the model key. Signal handlers restore on exit/error.
The currently integrated path reaches REPRODUCING, not a repair verdict.

Observed checks, final exit 0:
- `uv run pytest tests/unit/test_agent_harness.py tests/unit/test_worker_startup.py -q --junitxml=docs/evidence/m3-harness-unit.xml`: **25 passed**.
- Focused isolated PostgreSQL harness selection: **13 passed**, 1060 deselected,
  zero skips, 10.13s (`m3-harness-focused.xml`). This preceded the additional
  positive CLI configuration parameter; the full run includes that case.
- Full disposable-PostgreSQL regression: **1074 passed**, zero failures/errors/
  skips, **333.06s**, `m3-harness-postgres.xml`. One existing upstream
  Starlette/AnyIO warning. Task-owned local DB/roles cleaned by the harness.
- Global Ruff lint/format passed (194 files); strict mypy passed (97 source
  files). Scoped credential-value scan and Git diff checks pass.
- `uv run python -m scripts.check_checkout`: M2_FIXTURE_ACCEPTED.

Initial TDD run failed on the absent harness module (8 cases, exit 1).
The first integration collection exposed duplicate test basenames; the new
integration module was renamed without changing pytest mode or weakening tests.
Formatting/import findings were corrected. The final source has no suppressed
checks. The CLI description was corrected to describe opt-in inference after
starting the full run; that edit changes help text only.

The tiny integration fixture uses synthetic source/model HTTP, real PostgreSQL
collection-to-triage and replay/restart assertions. Failure mode confirms exactly
one outbound mock request across uncertain recovery. No inference/sandbox/Tavily
call or remote repository write, merge or deployment occurred. Public docs and
an official reference README were read online without provider credentials.
No dependency/UI changes required repeating prior audits/browser checks.

M3 remains NOT CLOSED. The remaining critical work is admitted baseline/candidate/
clean execution, trusted report production and live seven-gate acceptance, then
approval/publication and submission packaging. This closes the combined
collection/triage scheduling increment, not the whole runtime graph.


## 2026-10-22 — Requirements and tracking reconciliation

Reviewed tracking against implementation 715831d and retained acceptance artifacts.
Updated AGENTS.md with ongoing memory/harness/recovery, evidence tracking,
authorization and delivery-focus rules. Updated README's stale claims that UI and
agents were absent and all live integrations unverified. Current-focus metadata
now points to actual commits/evidence in todo.md. Added scope FR-16/FR-17 for the
user-requested memory/harness capabilities, executable contract pointers, dated
milestone/M3 status and runtime playbook links. Existing milestone exit criteria,
verification gates, approval and isolation requirements were preserved.

Corrected specs/contracts.md's illustrative gate status vocabulary to the actual
PASS/FAIL/MISSING schema. Unknown/blocked/unavailable evidence still never passes.
M3 remains open; M4 publication/evaluation and M5 deployment/submission remain open.
The next task is admitted execution/report collection plus trusted receipt
production and live seven-gate acceptance on a small fixture. The teammate owns
authentication. Earlier dated implementation-log entries remain historical.

Documentation validation: all 17 checked implementation/document paths exist;
retained JUnit parses as 1074 tests and zero failures/errors/skips; Markdown fences
are balanced; git diff --check passes (exit 0). Initial validation incorrectly
assumed console elapsed time must exactly equal JUnit time: console 333.06s vs
JUnit 333.023s. The corrected consistency check accepts the measured sub-0.1s
reporting overhead, without editing test evidence or any product/test threshold.
The offline check_checkout still returns M2_FIXTURE_ACCEPTED (exit 0).

Documentation-only change; the unchanged backend suite was not repeated.
No provider call, remote write, authentication code or runtime behavior changed.


## 2026-10-22 — Durable known-replay report collection

Implementation commit **c949e1e**, base d009dd3. Added the bounded read-only
observation reader, owner/lease/version/source/test/patch/consent fenced replay
collector and migration 0021 private terminal observation journal. Known IDs come
only from durable submission results. Only GETs are allowed; unknown submissions
are never guessed or repeated. Strict process state and exact bounded JUnit bytes
are retained independently of provider SUCCESS. Private report SHA256/canonical
record bindings, immutable runtime role and reservation-derived expiry apply.

Pending reads are not retained as terminal. Restart reconciles without HTTP.
A reproduced pending-vs-terminal race was fixed: validated durable terminal facts
win over an older pending read. Concurrent terminal reads produce one record/event;
conflicting terminal facts fail. Cancellation/lease/version changes during I/O
reject retention, and an audit failure rolls the private insert back. No checkpoint,
stage, budget or job is changed by observation retention.

Observed checks (final exit 0):
- `uv run pytest tests/contract/test_observation_reader.py -q --junitxml=docs/evidence/m3-observation-reader.xml`: **13 passed**, zero skips.
- Focused disposable-PostgreSQL collection run: **15 passed**, 1087 deselected,
  zero skips, 34.80s (`m3-replay-collection-focused.xml`).
- Additional atomic rollback/concurrent-read run: **2 passed**, 1102 deselected,
  zero skips, 7.89s (`m3-replay-collection-atomic.xml`).
- Full disposable-PostgreSQL regression with the existing 420-second outer
  harness override: **1104 passed**, zero failures/errors/skips, **373.37s console**,
  one existing upstream Starlette/AnyIO warning. JUnit suite time: 373.324s.
  Report: m3-replay-collection-postgres.xml, SHA256 `0d8bc9ccac92099d6c20b77ca6e967269f74a0bf31069c605a5beee5f7ebbaa8`.
- Global Ruff lint/format passed (199 files); strict mypy passed (99 source files).
  Scoped credential-value scan and git diff checks passed. The offline retained
  checkout checker still returned M2_FIXTURE_ACCEPTED.

Initial missing-module RED and pending/terminal race RED are retained losslessly
as m3-replay-collection-red.xml.gz and m3-replay-collection-race-red.xml.gz. No test
was weakened, skipped or suppressed. Task-owned disposable DB/roles were cleaned.
No dependency or UI changes required repeating their previous audits/browser runs.

Provider-status HTML was unavailable; the official index-linked OpenAPI spec was
read without credentials, alongside existing smoke response contracts. No live
inference/sandbox/Tavily request, remote repository write, merge or deployment
occurred. Actual report provenance in tests is synthetic HTTP and ephemeral test
capabilities, never proof of a production admitted runner. Auth remains unchanged.

M3 remains NOT CLOSED. Observations are unsigned and cannot authorize verification;
source/final-tree identity, OCI/image/configuration assessment, trusted receipt
production and live seven-gate acceptance remain required. The combined harness
still reaches REPRODUCING; this collector is an explicit separate internal action.
Next: independent workspace measurements and trusted receipt production, with
admitted baseline/candidate/clean execution and authentic UI evidence. Actual
assessed deployment facts/keys must not be invented or replaced with test keys.
Details and retention/privacy limits: docs/replay-collection.md.

## 2026-10-22 — bounded saved-image workspace measurement

Implemented actual file-byte measurement through the official image archive API,
without extraction, guest execution or signing. See `docs/workspace-measurement.md`
for the exact identity format, limits and remaining controller trust boundary.
Acceptance: 811 unit/contract tests passed in 5.96 seconds, exit 0,
`docs/evidence/m3-workspace-regression.xml`; strict mypy 100 source files passed;
Ruff lint/format passed. Initial oversized-test parameter naming exceeded Windows
per-test path limits; explicit short test IDs corrected the test harness.
No cloud execution, inference, credential access or remote writes occurred.
M3 remains open: this primitive is not yet wired to a trusted receipt producer.


## 2026-10-22 � independently measured execution receipt producer

Implementation commit: 40e2c40, based on 69651b4. Implemented the opt-in
MeasuredPreparedBackend using the existing admitted prepared REST backend.
Checks exact bindings/capabilities before provider reads; measures the actual
prepared image before POST and owned terminal workspace/report before signing.
Explicit worker-held Ed25519 keys use existing baseline/replay signature domains.
No expected hash is substituted for an observation. Mutation/nonzero exit/
timeout facts remain honest failures for the verifier; pending/malformed/missing
facts cannot yield receipts. Cached successful reads preserve signature/time.
Added a distinct PYTEST_FILES_MEASURED_V1 template with Python -B to avoid
bytecode changing the measured workspace. Old argv/plan identities are preserved.

Observed commands/results, all final exit 0:
- Initial focused contracts: 69 passed, m3-receipt-producer-focused.xml.
- Real disposable-PostgreSQL measured dispatch/restart composition: 2 passed,
  1143 deselected, 8.23s, m3-measured-dispatch-focused.xml. Exactly one POST for
  success and uncertain connection-loss paths; no new HTTP after restart.
- Full disposable-PostgreSQL regression through scripts.test_postgres with a
  480-second outer-suite override: 1145 passed, zero failures/errors/skips,
  419.35s console / 419.312s JUnit. One pre-existing Starlette/AnyIO warning.
  Report m3-receipt-producer-postgres.xml, SHA256
  2514150c0ced2ef82fc0271fad0d44997570a7777b586e3d7f64539777175c51.
- uv run ruff check; uv run ruff format --check: 204 files pass.
- uv run mypy: 101 source files pass.
- git diff --check and scoped credential-value pattern scan pass.
- uv run python -m scripts.check_checkout: M2_FIXTURE_ACCEPTED, authority false.

The initial module-absent RED is retained as m3-receipt-producer-red.xml.gz.
No tests, assertions or individual deadlines were weakened. The outer override
only accommodates the observed ~419-second aggregate suite; product execution
limits remain unchanged. Task-created disposable DB and roles were removed.
No UI/dependency changes warranted repeating unchanged browser/dependency audits.
No cloud inference/sandbox/Tavily call, external write or deployment occurred;
only synthetic HTTP/ephemeral test signatures were used. Auth remains untouched.

Threat review: key stays outside guests; admission precedes measurement; exact
owned IDs constrain reads; existing bounded transport/archive parser prevents
extraction and oversized input; provider stdout is discarded and raw JUnit stays
private. This is a low-level trusted-worker port, not a public signing endpoint.

M3 remains open. The producer currently loses its pre-measurement/receipt cache
on worker death: the sticky dispatcher prevents resubmission but cannot restore
this state. Durable producer retention/handoff is the next software task. Real
source/test/patch materialization, assessed OCI/provider/configuration mapping,
production key provisioning and all seven authenticated gates are still required
before live VERIFIED/UI acceptance. No manufactured PASS assessment/key was
installed. See docs/receipt-producer.md for the exact contract and limits.


## 2026-10-22 � durable producer-to-evidence and human review workflow

Implementation commit 9df1ac9 (base dc245ca). Migrations 0022/0023 add append-only
producer preparation/receipt and human decision/publication intent records.
DurableMeasuredBackend commits actual preparation before POST, restores only a
known journal operation and returns a receipt only after retention/audit commit.
Unknown submissions remain sticky. Corruption, expiry, current scope/fences and
signature/report/hash binding are rechecked; cached reads preserve timestamps.
ProducedReceipt uses bounded private base64 serialization. Completed producer
pairs now feed the existing signed replay/evidence reducer through a fenced
handoff that reconciles exact duplicates and leaves VERIFYING unchanged.

ApprovalJournal records exact independently authenticated human permission,
source/patch/verdict/target, nonce and <=900-second TTL. It rehashes retained
artifacts and current consent. Decision/checkpoint/audit commit atomically;
publication claim consumes one nonce into one stable external UUID/head branch.
Repeated/concurrent claims reconcile one intent. No GitHub implementation/public
approval endpoint/login was introduced; permission objects remain trusted
identity-adapter inputs. Authentication stays with its assigned owner.

Integration root cause fixed: RunMemory formerly required the current workflow
stage to equal its historical verifier verdict, so approve, reject and claim
made otherwise valid lessons fail MEMORY_INTEGRITY. Three focused cases failed
before the fix (m4-review-memory-red.xml.gz). Memory now requires matching durable
review/claim provenance for later stages, preserving original verifier outcome
and evidence expiry without recalling approval as authority. A forged later
stage without those records is still rejected. No exception was silenced.

Observed checks (final exit 0):
- Durable producer contracts: 17 passed, zero skips, 39.27s,
  m3-durable-producer-focused.xml.
- Producer/evidence handoff: 4 passed, 1183 deselected, 15.76s,
  m3-produced-delivery-focused.xml. Remote-shaped fake ports/signatures are
  explicitly synthetic and are not live runner acceptance.
- Approval journal: 21 passed, zero skips, 19.95s,
  m4-approval-journal-focused.xml.
- Combined approval/memory: 40 passed, 1150 deselected, 48.19s,
  m4-approval-memory-focused.xml; extra provenance negative: 1 passed,
  1190 deselected, 9.61s, m4-review-memory-negative.xml.
- Full disposable-PostgreSQL run through scripts.test_postgres with a 600-second
  outer aggregate ceiling: 1191 passed, zero failures/errors/skips,
  449.25s console / 449.212s JUnit, m3-m4-durable-workflow-postgres.xml.
  SHA256 d709171cdedbf5725f1446d330db593c0ff9f37d9dba604021d241e6282a9c2e.
- uv run ruff check; uv run ruff format --check: 213 files pass.
- uv run mypy: 105 source files pass. Scoped credential-value pattern and git
  diff checks pass. uv run python -m scripts.check_checkout still returns
  M2_FIXTURE_ACCEPTED with execution/verification authority false.

The first combined full run was intentionally interrupted after finding the
review/memory gap, before acceptance; its task-owned pytest launcher/interpreter
were stopped and the wrapper cleaned its database/roles. It is not a pass.
Initial absent-module REDs are retained losslessly as m3-durable-producer-red.xml.gz
and m4-approval-journal-red.xml.gz. Final run cleaned disposable DB/roles. No
individual test deadline, assertion, product budget or security gate was relaxed.
The aggregate ceiling only accommodates the measured growing suite. One existing
upstream Starlette/AnyIO warning remains. UI/dependency changes were absent, so
unchanged browser/dependency audits were not repeated.

Threat review: admission precedes provider measurement; preparation/audit failure
prevents POST; recovery cannot attach guessed IDs; records are immutable to the
runtime role; receipt keys remain outside guests. Every review/claim rechecks
current scope/bindings/TTL, verification custody and actual artifact integrity.
No serialized decision/intent grants publication on its own. Source content,
JUnit, credentials and hidden reasoning are not emitted in public events.

M2 fixed fixture exit stays accepted. M3/M4/M5 remain open: next is deterministic
seven-gate production/admitted materialization/full worker-UI flow; then actual
idempotent GitHub branch/PR/result recovery and hidden evaluation; then deployment
and submission packaging. Production signer/OCI/configuration assessment and
separate human identity integration remain deployment prerequisites. No cloud
inference/sandbox/Tavily call, external repository write, deployment or merge ran.

## 2026-10-23 — Bounded M4 publisher and durable result recovery

Implementation: 0042c9d; test correction: 7c88d10. No remote write, paid model,
sandbox or Tavily call, deployment or merge was performed. Authentication
interfaces stay with the teammate. The staged authentication specification was
preserved separately and is not part of these commits.

Added pure exact unified-diff application over bounded Git blobs, Git tree/commit
encoding validated independently by git hash-object --stdin (no object write or
source execution), actual fixed-origin GitHub tree/commit/ref/draft-PR REST
operations, per-POST current approval/permission/scope rechecks, deterministic
branch/commit/external-ID discovery and sanitized unknown-outcome handling.
No force-push, target-ref update, source execution, workflow edit or automatic
merge is available. Inputs come from checksum-verified retained approved evidence,
not caller-supplied diff JSON. Migration 0024 adds immutable publication results;
result/event/PR_OPENED update is atomic and late cancellation remains intact.
See github-publication.md for bounds, unsupported cases and partial-write risks.

Actual validation (exit 0 unless explicitly marked failed):

- Absent-module REDs retained losslessly: m4-publication-patch-red.xml.gz,
  m4-publication-objects-red.xml.gz and m4-github-publisher-red.xml.gz.
- Unicode line-separator regression exposed splitlines behavior; fixed using LF
  boundaries. m4-publication-unicode-red.xml.gz retains the failed observation.
- Focused publisher/text/object tests: uv run pytest tests/contract/test_github_publisher.py
  tests/unit/test_publication_objects.py tests/unit/test_publication_patch.py -q
  --junitxml=docs/evidence/m4-publisher-expanded.xml: 32 passed, exit 0, 2.14s.
- Disposable PostgreSQL focused test_publication selection: 31 passed,
  1192 deselected, 22.37s; m4-publication-custody-green.xml. Earlier two fixture
  mistakes (missing cancel CAS argument and unscoped count) are retained in
  m4-publication-custody.xml.gz, not claimed as passes.
- First full run: 1233 passed, one failed assertion, 449.16s, exit 1;
  m4-publisher-postgres.xml.gz. The actual UPDATE privilege denial was correct;
  the test expected the raw psycopg exception instead of StoreError's sanitized
  DB_FAILURE_42501. Focused reproduction is m4-publication-role-red.xml.gz.
  Corrected exact denial assertion passes (one test, 5.48s,
  m4-publication-role-green.xml). No permission, product code or gate was weakened.
- Final full run on 7c88d10 via scripts.test_postgres, disposable local DB/runtime
  roles, secure password injection without DSN output, aggregate subprocess
  ceiling 600 seconds: **1234 passed**, zero failures/errors/skips, **446.58s**,
  exit 0. JUnit m4-publisher-final-postgres.xml and its actual SHA256/JUnit time
  are recorded in m4-publication-checks.json. The wrapper cleaned test DB/roles.
  The ceiling changes no individual test deadline or product budget.
- uv run ruff check .; uv run ruff format --check .: pass, 224 files formatted.
- uv run mypy: pass, 111 typed core sources.
- Scoped credential-value patterns and scoped git diff --check: pass. The user's
  separately staged auth specification is untouched and excluded from scope.
- uv run python -m scripts.check_checkout: M2_FIXTURE_ACCEPTED, exit 0;
  verification/execution authority remains false.

One existing upstream Starlette/AnyIO deprecation warning remains. No dependency
or UI changes were made, so vulnerability audits/browser acceptance were not
repeated; these results do not claim deployment/hackathon readiness.

Threat review: credentials only in trusted HTTP headers, source/JUnit absent from
public events, actual Git bytes rehashed, symlinks/submodules/executable targets
rejected, exact patch scope preserved, fresh authorization before every POST,
no redirect/proxy inheritance/automatic write retry, immutable runtime custody,
and test-origin refusal. Provider HTTP and gate/human permissions are synthetic
in tests. Actual GitHub normalization, installation permissions and distributed
write/discovery behavior still require live acceptance. Partial objects can
remain after later denial; no destructive cleanup is automatic.

M2 fixed fixture remains accepted. M3/M4/M5 remain open. Next: deterministic
seven-gate production/admitted tiny-fixture worker flow, authentic assessed
signer/configuration, then independently authenticated exact review plus explicit
live write approval and one-PR discovery acceptance, hidden evaluation, and M5
packaging. No milestone exit was replaced with a weaker test.

## 2026-10-23 — GitHub authentication assessment and proposed implementation plan

Read the user-supplied GitHub authentication specification and inspected existing
LocalAuth/API routes, local startup, Next.js shared-bearer bridge, migrations,
approval and publication interfaces. Rechecked official GitHub user token/PKCE,
refresh and installation token documentation. Created tasks/plan.md and proposed
ADR 014; root todo.md records planned unchecked slices and changed ownership.

Key integration findings: the current BFF token maps every local browser to one
operator; it cannot serve as GitHub-session identity. Internal owner/repository
UUIDs must be explicitly mapped to GitHub numeric IDs without taking over legacy
runs or replacing immutable execution grants. Existing approval/result journals
are reusable through trusted current identity and repository permission loaders.

Plan/ADR existence checks and scoped git diff --check pass, exit 0. No application
code, dependencies or database schema changed; tests were not rerun for this
planning-only increment. No provider credentials, authenticated API calls, remote
writes or paid services were used. Offline slice A can begin without keys; real
browser acceptance later requires registered App/client settings and human login.
No authentication acceptance checkbox or milestone was marked complete.

## 2026-10-23 — GitHub OAuth/session foundation accepted locally

Implementation 51e4343. Typed explicit settings and bounded GitHub App user client,
S256 PKCE URLs, code exchange and stable numeric identity; migration 0025 and
AuthJournal for browser-bound single-use attempts, encrypted credentials, hashed
opaque sessions, rotation/logout and append-only security audit. Separate
create_auth_app remains loopback-only, exposes start/callback/me/logout, has
trusted Host/CORS/cookie/CSRF boundaries and defaults to refusing synthetic session
authority. Existing LocalAuth API and bridge were preserved. No live GitHub call,
credential discovery, external write, paid provider call or deployment occurred.
See github-authentication.md for interfaces, key/role/retention limits and gaps.

Actual evidence, commands and exits:
- Absent-module REDs: auth-client-red.xml.gz, auth-journal-red.xml.gz and
  auth-routes-red.xml.gz (exit 1); preserved losslessly, never called passes.
- Client first green: 21 passed in 0.22s, auth-client-green.xml. Initial DB/client
  selection: 28 passed, 7.07s, auth-journal-focused.xml. HTTP selection: 35 passed,
  7.08s, auth-http-focused.xml. All exit 0 with synthetic provider identities.
- Durable rate limiting exposed cross-test peer-state leakage. First focused run
  failed one test (39 passed), auth-foundation-focused.xml.gz. Journal fixtures
  now use distinct simulated peers. An expanded run later hit the real ten/minute
  rate limit at the HTTPS-cookie test because browser tests shared an IP/bucket.
  Targeted reproduction: auth-rate-isolation-red.xml.gz, one failed, ten passed,
  7.95s. App tests now isolate only their loopback peer/action rate buckets using
  disposable migration authority. Product limit/permission/assertions unchanged.
- The first aggregate attempt was stopped after that fixture failure: only the
  verified task-owned pytest launcher/interpreter were terminated; the wrapper
  cleaned its random database/roles. It has no final JUnit and is not a pass.
- Final focused selection via scripts.test_postgres -k auth_app/auth_journal/
  github_auth: 43 passed, 1234 deselected, 19.76s, exit 0,
  auth-foundation-final-focused.xml. Covers cookie flags, replay/browser binding,
  uncertain exchange, concurrent finalization, purpose/key tampering, identity
  isolation, durable rate caps, session expiry/rotation and CSRF/audit behavior.
- Final full disposable PostgreSQL regression at 51e4343, outer aggregate ceiling
  600 seconds: **1277 passed**, zero failures/errors/skips, **429.84s** console,
  exit 0. auth-foundation-final-postgres.xml; exact JUnit time/checksum recorded
  in auth-foundation-checks.json. All random test DB/roles cleaned by wrapper.
- uv run ruff check .; uv run ruff format --check .: pass (233 files).
  uv run mypy: pass (116 typed source files). Scoped credential-value pattern
  scan and scoped git diff --check pass. Existing upstream Starlette/AnyIO warning
  remains. No new dependency or UI changes; those audits were not repeated.

Threat review: code/verifier/client secret only in bounded POST bodies, user token
only in provider headers, no provider error bodies retained; hashes/encryption
separate domains/keys; attempts claim before exchange, no blind retries; only
numeric identities link accounts; no legacy owner adoption; DB session/revocation
checks and host-only secure production cookie shape; test provenance denied by
default. Dedicated deployment auth-role/key provisioning, retention/keyring
rotation, refresh brokerage and public proxy/rate setup remain open. The test
role combines permissions solely for disposable contracts, not deployment.

A/B local foundation tasks are accepted. C's me/logout/CSRF is implemented but
existing private API integration is not. D repository authorization, E approval/
installation brokerage and F browser/live acceptance remain open. M3/M4/M5 exits
are unchanged. Work continues on C/D using the actual existing owner/grant APIs.


## 2026-10-23 - Session/repository API and browser integration

Implementation 638feb6 (source parent16fe2ca). Session identity now composes with
existing owner-filtered run/SSE/evidence routes, current app/user repository checks,
exact-commit consent and immutable UUID mapping. Owner/CSRF disconnect atomically
clears execution consent; reconnect creates a fresh grant. No bearer fallback.
Separate local startup validates restricted roles/config, but actual separated-role
startup acceptance remains pending. Next.js GitHub mode adds login/success/session
identity/logout and repository selection; session-only BFF, in-memory CSRF, no
browser credential storage. Connected grant/source prefill existing workbench.

Actual checks, exit0 unless stated:
- Focused scripts.test_postgres with pytest -k 'session_access or auth_repositories
  or auth_local':22 pass,1277 deselected,13.16s,auth-session-disconnect-green.xml.
- Full disposable PostgreSQL regression:1299 pass,zero failures/errors/skips,
  417.46s,auth-session-final-postgres.xml. Wrapper's aggregate subprocess deadline
  was600s; no individual/product timeout weakened. Temporary DB/roles cleaned.
- uv run ruff check .; uv run ruff format --check .:pass240files; uv run mypy:
  pass119sources. uv lock --check, scoped credential-pattern and diff checks pass.
  Dependencies unchanged; vulnerability audit not repeated.
- npm run lint; npm run typecheck; npm run build:pass, Next16.4.2 production build.
  npx playwright test -c playwright.auth.config.ts:3pass5.2s;
  npx playwright test:6pass/1skip29.8s. Opt-in real local API case was not enabled.
  auth-session-browser.xml and auth-local-mode-browser.xml retained. Desktop login
  and390px mobile connection screenshots visually inspected, synthetic data only.
- RED disconnect missing route404 preserved in auth-disconnect-red.xml.gz. Earlier
  query/path strict integer422 fixed with separate protocol numeric validation.
  Composition's replacement StoreError handler turned owner404 into503; preserving
  core handler fixed it. Earlier failures compressed without deleting history,
  including misleading historical 'green' names. Initial browser server startup
  timed out; owned listener checked/stopped and ordinary rerun passed, unchanged
  timeout limits. Upstream Starlette/AnyIO deprecation warning remains.

Threat review: parameterized owner SQL, no legacy adoption, bounded lists, provider
origin checks, read-only user-token calls, explicit consent and current access.
Workers receive no new grant-write/auth permissions. Provider denial blocks new
API work but automatic stored-grant revocation mirroring is not yet implemented.
API-only disconnect clears consent; it cannot prove already-running compute stops.
Credentials expire to reauthentication, no blind refresh retries. Public deployment,
keyring/retention/refresh, real App login, actual restricted-role startup and plan E
session-derived exact approval/scoped installation-token brokerage remain open.
No actual GitHub credentials read, authenticated provider request, paid call,
external write, PR, merge or deploy occurred. M3/M4/M5 remain open.


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

2026-10-23 repository selector diagnosis: authorized read-only provider calls
returned one installation and zero repositories. GitHub installation settings
independently show No permissions / No repositories. This is configuration,
not a populated provider list being lost by the UI. No permission changed.
User must set App Contents read-only, approve updated installation and select
maf_ds_agent. See auth-installation-scope-checks.json and screenshot.


## 2026-10-24 - Large GitHub account discovery and simpler onboarding

Implementation b169a81 (parent7359f72). Prior necessary authorized metadata reads
found367 repositories after App access was granted; old total_count<=50 validation
rejected that legitimate account. Provider errors mapped to401 then caused the UI
session gate to show login. No raw provider response or secret retained.

Added explicit repository page1..20000,50 entries/512KiB maximum per provider page,
existing HTTP deadline, duplicate/partial-page rejection and safe502 discovery
errors. Legacy no-page enumeration remains bounded. Migration0027 stores the page
lookup hint; every source connect/private access still checks actual current user/
installation membership. Fresh same-source reconnect may update the page locator
without changing immutable grant/source/owner/provenance/private consent. Wrong
page, installation and repo fail. No broader credential fallback or worker grants.
Added membership-checked default-branch commit metadata resolution, not code fetch.
Next.js auto-selects a sole installation, pages/filters current results, refreshes
with explicit empty/error states, pins source automatically, keeps Advanced manual
SHA, and opens the prefilled incident form after connection. GITHUB_APP_SLUG is
server-configured/validated for the fixed GitHub Manage access link. No execution
is dispatched automatically. Corrected a refresh race that cleared repo errors
when installation enumeration completed after repository failure.

Actual checks:
- RED auth-pagination-red.xml.gz proves missing paged interface; synthetic367
  account test proves first50/last17 and exact-page membership, plus duplicate/
  truncation/out-of-range negatives. Focused integration initially exposed old
  OAuth timeout status assertion401 (now502; replay remains401), then same-source
  locator upgrade conflict. Both fixed with retained reports and unchanged gates.
- uv run python wrapper for scripts.test_postgres, pytest -k auth_app/session_access/
  auth_repositories/github_auth:53 pass,1252 deselected,26.88s,exit0;
  auth-onboarding-focused.xml. Three additional page negatives included in full run.
- Full scripts.test_postgres wrapper (600s aggregate process deadline):1308 pass,
  zero failures/errors/skips,485.05s,exit0,auth-onboarding-final-postgres.xml.
  Temporary databases/roles cleaned by wrapper. No individual/product timeout or
  expected security denial was weakened. Upstream Starlette/AnyIO warning remains.
- uv run ruff check .; uv run ruff format --check .:pass241files; uv run mypy:
  pass119sources. uv lock --check, git diff --check and scoped credential-value
  scan pass. No dependency changed; dependency vulnerability audit not repeated.
- npm run lint; npm run typecheck; npm run build:pass. Final npx playwright test -c
  playwright.auth.config.ts:4 pass7.6s (BFF query validation, auto-selection/pinning,
  connect-to-prefilled-intake, paged refresh failure without logout). Existing
  npx playwright test:6 pass/1 opt-in real-API skip19.8s. Final CSS button/layout
  refinement revalidated by build/auth browser; mobile screenshot visually reviewed.
  Failed browser tests retained compressed: real error-clearing race and then a
  test locator collision with Next's empty route-announcer. Scoped locator fixed;
  assertions were not removed. Evidence hashes are in auth-onboarding-checks.json.

Applied0027 only to the task-created rf_auth_local database through its retained
separate migrator. No other database/role changed. API8000 and production UI3000
restored for user check. Browser URL security policy denied inspecting the user's
existing tab; no alternate browser/HTTP/CDP workaround was attempted. Isolated
synthetic browser tests use separate fixtures and do not claim live acceptance.
A text question requests user reload/confirmation. Current live selector outcome
is NOT_RUN until observed/admitted or confirmed by user. No new live repository
connect, inference, sandbox, GitHub write, PR, merge or deploy in this acceptance.

Remaining: stale provider ordering requires refresh/reconnect; filtering is page-
local; installation enumeration cap remains50. M3 seven-gate production/worker
journey, session-derived exact approval and installation-token brokerage, M4 live
approved PR/hidden evaluation and M5 packaging/deploy remain open. Existing
milestone exits and human approval boundaries are preserved.
