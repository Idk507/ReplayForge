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
