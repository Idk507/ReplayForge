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
