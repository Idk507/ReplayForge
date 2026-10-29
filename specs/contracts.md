# Versioned Contracts and Verification Predicates

## JSON contract examples (illustrative schema, not implemented endpoint responses)

```json
{
  "run_id": "run_01",
  "schema_version": "1.0",
  "source_commit": "0123456789abcdef0123456789abcdef01234567",
  "state": "VERIFYING",
  "version": 12,
  "budget": {"max_model_calls": 15, "max_patch_attempts": 3, "max_commands": 20},
  "artifact_ids": ["art_baseline", "art_patch"]
}
```

```json
{
  "run_id": "run_01",
  "attempt": 2,
  "gate": "CLEAN_REPLAY_PASS",
  "status": "PASS",
  "execution_id": "exec_729",
  "evidence_artifact": "sha256:abc123-example-not-a-real-hash",
  "source_commit": "0123456789abcdef0123456789abcdef01234567"
}
```

The actual implementation must enforce 64-digit hexadecimal SHA256 for hashes and actual valid Git SHA. The implemented `GateEvidence.status` is `PASS`, `FAIL` or `MISSING`
(`services/verifier/gates.py`); unknown/unavailable/blocked observations cannot
be represented as PASS. The examples above are illustrative, not wire schemas. The verifier computes verdict from persisted observations whose execution IDs refer to jobs with matching source SHA, patch digest, runner image digest and test digest.

## Invariants to property-test
An invalid transition cannot move to VERIFIED; missing mandatory gate implies not VERIFIED; corrupted evidence cannot satisfy a gate; reproduction failing only due to setup cannot satisfy baseline; authorization failure cannot be overridden by agent output; cancellation is terminal; approval cannot be reused; PR operation is idempotent; run cannot be approved when commit/patch/policy changed; untrusted content cannot create a privileged tool invocation; strict budget cannot become negative; a tenant cannot read another tenant's artifact.

## HTTP behavior
201/202 for accepted or created jobs, 400 bad request, 401 no identity, 403 forbidden scope, 404 unknown or inaccessible ID (avoid information leaks), 409 stale run/approval/digest, 422 schema violation, 429 rate limit, 503 dependency unavailable. Supply stable error code and correlation ID without exposing sensitive stack traces.


## Implemented memory and harness contracts — 2026-10-22

`RepairLesson`/`MemoryRecall` in `packages/contracts/memory.py` carry bounded
historical outcome, failed/missing gate names, exact source/patch/verdict/manifest
references and expiry. Owner/repository/policy/commit consent is enforced by
RunMemory. Canonical record/hash binding is checked on recall. Memory cannot
execute, verify or publish, and cannot displace current context beyond the
existing 8-entry/4096-byte request cap. No semantic accuracy claim is implied.

`HarnessResult` in `services/orchestrator/harness.py` reports scheduling counts,
configuration and stop state. Each step attempts at most one fixed-stage claim;
collection-to-triage uses existing durable transactions. Failed/uncertain provider
requests propagate without automatic retry. The CLI validates explicit inference
permission and a matching declared cost policy before stage claims. Completed
triage reconciles its checkpoint rather than issuing another inference request.
The implemented flow reaches REPRODUCING; all original verifier/approval
requirements remain mandatory for the later repair/publication path.

Executable contracts: `tests/unit/test_memory_context.py`,
`tests/integration/test_memory.py`, `tests/unit/test_agent_harness.py`,
`tests/integration/test_harness_handoff.py`, and worker startup/recovery tests.


## Known replay observation contract — 2026-10-22

`ReplayObservation` (schema 1.0, services/runner/observations.py) carries PENDING,
REPORT_COLLECTED or TERMINAL_NO_REPORT. Provider status and strict process-state
facts are separate from a verification verdict. Optional private JUnit bytes are
bounded to 65536, base64-encoded and checked against the actual SHA256. Records
carry exact run/execution/operation/image/plan/capability identities, origin,
trace and observation time; execution/verification authority is always false.

The durable collect_replay controller accepts an existing leased run and closed
replay phase, never a caller-supplied operation UUID. Submission identity is
loaded from the journal. Current bindings/consent are checked before and after
I/O. Terminal record plus audit event commit atomically; expiry is inherited
from the reservation. Cached reads verify canonical bytes/checksum/bindings and
perform no HTTP. A committed terminal beats an earlier pending read; conflicting
terminal facts are rejected. This contract does not prove fresh compute, tree
identity, image OCI mapping, signing authenticity or physical cleanup.


## Measured prepared receipt production � 2026-10-22

The opt-in PYTEST_FILES_MEASURED_V1 command saves the result image and inserts
Python -B before the existing isolated pytest template. Old command argv/IDs
remain unchanged; no-bytecode is part of the new exact plan hash. Both the
transport and durable report reader accept the new closed command ID.

MeasuredPreparedBackend reuses the admitted prepared submission port. A trusted
worker injects the receipt signing key; neither guests nor models receive it.
Actual pre/post saved-image archives and JUnit bytes determine the signed facts;
expected hashes are only comparisons. Baseline/replay signature domains remain
separate. TEST_TRANSPORT remains test evidence regardless of which key signs it.
ProducedReceipt is private, not a verdict or publication authority. Repeated
successful reads preserve the original signature/time without I/O. Durable
producer-state restoration remains open; sticky dispatch never repeats a POST.
See docs/receipt-producer.md for trust inputs, limits and executable evidence.


## Durable producer and approval contracts � 2026-10-22

ProducerJournal persists exact measured preparation before submission and private
base64 ProducedReceipt records after collection. Canonical bytes/checksum,
configured signature key and actual report hash are revalidated at fenced reads.
Expiry inherits the reservation. completed() resolves provider IDs from durable
submission state; at_version() only rebinds the current CAS fence. Neither can
authorize another POST. deliver_produced_replays consumes the two completed
journals through the existing authenticated pair/evidence reducer, never a verdict.

ApprovalJournal records one HumanDecision per verified run under current human
permission/consent/source/patch/target/TTL checks. claim() consumes its nonce into
one immutable PublicationIntent with a durable external UUID/head branch.
Concurrent/exact retries return that intent, without refreshing approval or
creating another consumption. The intent cannot authorize publication on its
own; no public approval route or GitHub writer is installed. Migrations 0022 and
0023 require append-only runtime grants. See durable-producer.md and
approval-journal.md for recovery limits, privacy and test provenance.

## GitHub publication adapter — 2026-10-23

publish_approved accepts only trusted identity and publisher dependencies. It
loads the exact retained reviewed patch through PublicationJournal and rechecks
approval/consent/current grant before every HTTP POST. GitHubPublisher computes
exact Git trees/commits from pinned bounded regular text files without execution;
only a deterministic new branch and draft PR are written. Unknown outcomes use
explicit admitted rediscovery, never force-push or an alternative branch.
Migration 0024 appends actual remote result/event/checkpoint atomically and keeps
late cancellation intact. TEST transport cannot become PR_OPENED evidence.
No public route, auth implementation, model tool or default harness dispatch was
added. See docs/github-publication.md for bounds, recovery and live acceptance.
This supersedes the earlier statement that no writer implementation is installed;
no live writer acceptance or M4 completion is claimed.

## GitHub identity foundation — 2026-10-23

Implementation 51e4343 adds a separate typed auth app, bounded GitHub App user
OAuth client, browser-bound one-use journal and opaque session custody. Migration
0025 adds auth-specific tables without reassigning existing owner/repository UUIDs.
No execution grants or reviewer roles result from login. Existing local bearer
API and bridge are preserved; session-principal/repository authorization and UI
integration remain open. See docs/github-authentication.md for exact boundaries,
key/retention limits, trusted dependencies and acceptance provenance.


## GitHub session/repository continuation - 2026-10-23

The explicit session API preserves owner-filtered run/SSE/evidence checks and
adds current user/installation repository authorization. Next.js GitHub mode
forwards session cookies plus Origin/CSRF, never an operator bearer. Numeric
repository connection requires exact source and explicit execution/private
consent. Owner disconnect atomically revokes stored execution consent. These
local contracts do not grant reviewer permission or authorize publication.
See docs/github-authentication.md for API routes, startup, limits and evidence.


## Repository onboarding follow-up - 2026-10-24

Explicit session-mode repository pages and default-branch SHA resolution now
support large accounts without implicit full enumeration. Migration0027 stores a
provider page locator; fresh membership checks, immutable source/private consent
and owner checks remain mandatory. Provider errors do not impersonate expired
browser sessions. One-time App/DB configuration belongs to operators; end users
choose a repository and explicitly consent. See docs/github-authentication.md for
bounds, stale-page recovery and final acceptance evidence. M3/M4 exits unchanged.


## Authenticated collection handoff - 2026-10-24

See docs/authenticated-collection.md for the opt-in loopback broker and isolated
worker startup. Selected reads preserve registered grants. Live small-file
collection reached TRIAGING; this does not grant VERIFIED or close M3/M4.
No inference, repository execution or publication is enabled by this slice.


## Authenticated triage continuation - 2026-10-24

Implementation4917e5c connects queued collection to the existing durable triage
controller without worker GitHub/vault credentials. Triage requires explicit
inference opt-in and a declared cost; default collection remains inference-off.
Current membership, lease/version/consent and sticky request journals remain
mandatory. See docs/authenticated-triage.md. Local fake-provider acceptance is
not live inference, baseline execution, VERIFIED or M3/M4 completion.


## Admitted baseline continuation - 2026-10-25

Implementations e78ea60/a061648 connect reproduction retention to a stage-specific
baseline queue and one-shot prepared-workspace dispatch. Collection workers do
not claim baseline jobs. New migration0028 preserves immutable submission claims;
nonempty-ledger rollback remains refused. See docs/baseline-worker.md for actual
REST/SDK findings, prerequisites and limitations. Tests use synthetic assessors
and fake HTTP; no live VERIFIED or M3/M4 completion follows from submission.


## Concrete baseline provider - 2026-10-25

Implementation80d544f supplies NebiusBaselineProvider with exact per-run claim
selection and immutable input validation. See docs/baseline-worker.md. Resolution
performs no provider request and does not replace actual materialization or
collector assessment. Live VERIFIED and production readiness remain unaccepted.

Session review/publication2026-10-25: contracts in docs/authenticated-approval.md.
Review recovery preserves original publication_version/TTL. Serialized receipts
never grant write authority. Human review, scoped minting and each publisher POST
reload trusted authority. M3/M4 live acceptance requirements remain unchanged.

Materialization custody2026-10-25: see docs/materialization.md. Source/test archive
identity is distinct from OCI identity/capability trust. Recovery preserves
original timestamp/expiry and cannot repeat unknown POST. M3/M4 exits unchanged.


Confined execution follow-up2026-10-25: docs/confined-runner.md defines the
opt-in measured pytest supervisor, exact payload binding, protected child-result
collection and durable candidate/clean recovery. It does not supply a real
capability profile or enable default live execution. Digest-pinned image import
failed HTTP404 after SDK URL normalization; actual full repair/replay, all seven
gates, exact human-approved PR and hidden evaluation remain open. Original
M3/M4 exits are unchanged.

## Operator image-import lifecycle - 2026-10-26

Migration0031's `image_import_attempts` and append-only
`image_import_events` are an internal operator-side ledger, separate from a run
verdict. A unique `(project_id, source_digest)` prevents resubmitting a digest
after an uncertain or rejected POST. The exact request digest also binds the
bounded timeout. The one-shot adapter must commit the intent before POST, commit
the provider operation UUID immediately after a valid 201, and use only bounded
GET polling for that known operation. Terminal provider image UUID/status and
safe diagnostics are retained without response bodies or credentials.

Recovery checks event sequence/checksums, source/project/request identity and
lifecycle order. Event checksums detect accidental corruption, not a malicious
database owner; they are not signatures or trusted runtime receipts. This
operator journal is not currently wired to production admission/startup. Fake
transport plus disposable PostgreSQL acceptance proves local composition only;
it does not prove an import, OCI provenance, runtime capability, assessor or
collector trust, remote execution, or any M3 gate. See `docs/confined-runner.md`
and `docs/m3-luna-recovery-plan.md`.

The adapter now consumes a typed `ImageImportLifecycle` exposing project, request,
state and operation identity. It rejects mismatched project/request bindings
before POST, and GET recovery requires the exact journal operation in
`OPERATION_KNOWN`. This is a local API contract, not production admission.

The operator command `scripts.import_runner_image` is offline by default and
requires `--live` to create a provider operation. It requires the dedicated
`REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL` plus Nebius project credentials. On
restart it reads the journal first: only an `OPERATION_KNOWN` entry may issue
GET; INTENT/SUBMISSION_UNKNOWN/PROVIDER_REJECTED never cause a POST. This tool
still does not check a qualified execution runtime or authorize sandbox code.

The operator controller reports provider import lifecycle only (NOT_RUN, pending,
uncertain, rejected or provider-terminal status); it never yields VERIFIED and
never converts a provider image UUID into capability evidence. The CLI is
explicitly gated by `--live`, and the qualified execution admission path remains
an independent required control.


Image-import terminal results may retain only a fixed failure code, upstream HTTP
status and raw-error SHA256; raw provider errors are excluded. Old persisted
terminal events remain readable with absent optional fields and are not rewritten.
Cached terminal/uncertain reports preserve their original observation timestamp.
Polling bounds (strict integer count/time and finite bounded interval) are
validated before database access or POST. These facts still grant no sandbox
execution or M3 verification authority.

### Confined baseline controller continuation - 2026-10-27

ConfinedBaselineProvider is an internal trusted factory for an exact measured
BASELINE plan and confined configuration; its construction performs no provider
I/O or trust discovery. advance_confined_baseline requires an existing owned
lease and uses durable baseline dispatch/producer records, performs one bounded
collection pass, and never repeats an unknown submission. TEST_TRANSPORT returns
TEST_EVIDENCE_RETAINED without granting PATCHING. Produced baseline delivery
accepts no caller-provided report/status/operation and rejects test-origin
carriers; the existing reducer owns stage acceptance. Pending work requires a
worker driver with safe heartbeats, not repeated lease acquisitions. Current
acceptance is synthetic and does not establish qualified runtime or M3/M4 exits.

## Signed gate payload schema 2

Signed gate payload schema 2 (`replayforge.verification-gates.v2`) requires an
explicit phase, image digest, configuration reference, operation UUID where the
gate is an execution, content-addressed artifact reference, source commit, patch
reference and workspace digest on each `GateEvidence`. Gate-to-phase mapping is
fixed in application code. `GateVerifier` checks each phase record against the
run's pinned image/configuration/source/patch/workspace and evidence manifest,
and requires distinct execution IDs, provider operation IDs and report artifacts
for baseline, patched regression, existing suite and clean replay. The v1 signed
gate purpose is rejected by the v2 model.

This is a binding/verification contract, not a production gate producer. The
producer that reads durable phase journals and creates a signature under an
independently provisioned key remains unimplemented. Current context still
supplies one run-wide image/configuration/workspace value; distinct phase
configuration and workspace expectations must be loaded from durable phase
records before live acceptance. Synthetic signer tests do not establish any
production gate or VERIFIED verdict.

## Signed gate payload v3 — 2026-10-28

Schema 3 (`replayforge.verification-gates.v3`) adds required phase-specific
image, configuration, and workspace bindings for all seven mandatory gate
phases. Gate records are checked against their phase binding; distinct
workspaces/configurations are allowed across baseline, candidate, existing
suite, clean replay, and local policy gates. Baseline image/configuration also
must match the legacy run-level fields. The seven required gate names and verdict
policy are unchanged. See `docs/verification-gate-v3.md`.

This improves exact binding in the consumer. It does not implement the
production phase-journal loader or signing producer. No production gate keys,
independently trusted operation correlation, separate existing-suite execution
path, or live seven-gate evidence exists. Synthetic tests do not grant VERIFIED.
