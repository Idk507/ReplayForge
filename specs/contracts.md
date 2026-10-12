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

The actual implementation must enforce 64-digit hexadecimal SHA256 for hashes and actual valid Git SHA. `gate.status` is one of `PASS`, `FAIL`, `UNKNOWN`, `BLOCKED`; unknown never counts as pass. The verifier computes verdict from persisted observations whose execution IDs refer to jobs with matching source SHA, patch digest, runner image digest and test digest.

## Invariants to property-test
An invalid transition cannot move to VERIFIED; missing mandatory gate implies not VERIFIED; corrupted evidence cannot satisfy a gate; reproduction failing only due to setup cannot satisfy baseline; authorization failure cannot be overridden by agent output; cancellation is terminal; approval cannot be reused; PR operation is idempotent; run cannot be approved when commit/patch/policy changed; untrusted content cannot create a privileged tool invocation; strict budget cannot become negative; a tenant cannot read another tenant's artifact.

## HTTP behavior
201/202 for accepted or created jobs, 400 bad request, 401 no identity, 403 forbidden scope, 404 unknown or inaccessible ID (avoid information leaks), 409 stale run/approval/digest, 422 schema violation, 429 rate limit, 503 dependency unavailable. Supply stable error code and correlation ID without exposing sensitive stack traces.
