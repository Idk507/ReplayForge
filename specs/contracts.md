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
