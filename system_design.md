# ReplayForge — Detailed System Design

## 1. Request lifecycle and transactional sequence

```mermaid
sequenceDiagram
 participant UI as Browser
 participant API as FastAPI
 participant DB as Postgres
 participant WF as LangGraph Worker
 participant N as Nebius Nemotron
 participant X as Isolated Runner
 participant T as Tavily
 participant GH as GitHub
 UI->>API: POST /runs + idempotency key
 API->>DB: scope check; insert run+outbox
 API-->>UI: 202 run_id
 WF->>DB: claim job lease
 WF->>GH: fetch pinned authorized commit (read)
 WF->>N: triage hypothesis
 opt upstream documentation needed
 WF->>T: sanitized package/version query
 T-->>WF: sources with provenance
 end
 WF->>N: generate reproduction test
 WF->>X: baseline test on original SHA
 X-->>WF: observed targeted failure + artifact hashes
 WF->>N: propose bounded patch
 WF->>X: apply patch + tests, fresh tree
 X-->>WF: candidate observations
 WF->>X: independent clean replay
 X-->>WF: replay observations
 WF->>DB: append deterministic gate results + manifest
 API-->>UI: SSE run events
 UI->>API: explicit approval for patch digest
 API->>DB: store expiring one-use approval
 UI->>API: request PR creation
 API->>GH: constrained branch + PR (idempotent)
 GH-->>API: PR identifier
 API->>DB: persist result and audit
```

## 2. Event sourcing and concurrency
Use Postgres as source of truth. UI state derives from persisted `run_events` rather than in-memory worker messages. `sequence` is monotonically increasing per run. An API command validates expected run `version`, commits a state transition and event together, then sends outbox notification. Leases use `SELECT ... FOR UPDATE SKIP LOCKED` plus heartbeat and uniqueness; expired leases may be reclaimed, but external side effects must have idempotency keys and reconciliation. The same execution command must never be charged twice merely because SSE reconnects. Use optimistic concurrency on approvals.

## 3. Evidence integrity and reproducibility
Evidence manifest fields: `schema_version,run_id,original_commit,repository_snapshot_sha256,base_image_digest,dependency_lock_sha256,incident_report_digest,generated_test_sha256,baseline_execution_id,baseline_signature,patch_sha256,patched_execution_id,clean_replay_execution_id,required_gates,tool_event_refs,model_usage_refs,external_citations,policy_version,created_at,manifest_sha256`. Artifacts are immutable blobs addressed by SHA256. Compute manifest hash from canonical JSON excluding self-hash. Keep provenance directed links between inputs, execution outputs and verdict. GitHub PR body links to the sanitized report and quotes immutable hashes, not potentially expiring raw logs.

## 4. State truth table
| Baseline target fail | Patched regression | Existing tests | Fresh replay | Policy | Verdict |
|---|---|---|---|---|---|
| true | true | true | true | pass | VERIFIED |
| false | any | any | any | pass | INCONCLUSIVE |
| true | false | any | any | pass | NOT_VERIFIED |
| true | true | false | any | pass | NOT_VERIFIED |
| true | true | true | false | pass | NOT_VERIFIED or INCONCLUSIVE if infrastructure fault |
| any | any | any | any | denied | POLICY_BLOCKED |
No 'verified with warnings' loophole for mandatory gates. An optional gate warning must be documented with policy version.

## 5. Data transfer formats
Workers exchange JSON typed envelopes, never executable serialized Python objects. `ToolInvocation{invocation_id,run_id,attempt_id,tool_name,args,deadline_ms,policy_hash}`; `ToolObservation{invocation_id,status,exit_code?,stdout_artifact?,stderr_artifact?,started_at,finished_at,runner_image_digest,trace_id}`. Tool command arguments are arrays of literal tokens or validated test selectors, never shell command strings. OpenAI-compatible requests contain redacted text and a tool-specific response JSON schema. Define strict file and byte quotas.

## 6. Verification DAG
A candidate cannot begin until baseline reproduction is valid. Candidate tests, lint, static vulnerability scan and patch policy can run independently against same immutable candidate tree; fresh replay runs only after candidate passes. Every gate records command, inputs, result, timestamps and digest. The final verdict is recomputed from persisted gate observations rather than stored as a model-set mutable boolean. An evidence import that claims `PASS` without corresponding observed execution cannot satisfy verifier.

## 7. Cost and latency control
Each run reserves an estimated spend budget and command budget on creation. Before every inference call, calculate conservative projected tokens; after completion commit actual provider usage if returned. Assign triage and patch token caps independently. Use bounded exponential backoff honoring 429; don't retry non-idempotent writes blindly. Limit parallel sandbox count to provider quota, use queue admission, and end with budget-exceeded reason when limits are crossed. Show model, tokens, duration and estimated cost as observed metrics, not guaranteed prices.

## 8. Failure catalog
`R_INPUT_INVALID`, `R_REPO_UNAUTHORIZED`, `R_SECRET_REDACT_FAIL`, `M_AUTH`, `M_RATE_LIMIT`, `M_SCHEMA_INVALID`, `S_UNAVAILABLE`, `S_TIMEOUT`, `S_POLICY_DENIED`, `T_BASELINE_INVALID`, `T_REGRESSION_FAILED`, `T_FLAKY`, `A_MANIFEST_MISSING`, `A_CHECKSUM_MISMATCH`, `G_APPROVAL_EXPIRED`, `G_PR_DENIED`, `G_PR_TRANSIENT`, `B_BUDGET_EXHAUSTED`. Error object: `code`, `retryable`, `user_message`, `internal_correlation_id`, `safe_details`. All failures are redacted to users and detailed enough in private logs to debug.

## 9. Multi-tenancy and scale progression
First release can be single-tenant with enforceable user roles and a tenant_id in every table. Do not rely on single-tenant assumptions in storage keys or queries. API replicas are load balanced; Postgres constrains job claim; sandbox capacity reservation enforces backpressure and per-tenant fairness. Move to a dedicated queue service only when measured DB queue contention demands it. Use object-store lifecycle rules; clean sandbox within minutes of terminal state. Run inference gateway with per-provider concurrency and circuit breaker.

## 10. UI interaction contract
Run timeline has stage statuses `pending/running/pass/fail/blocked/inconclusive`, each anchored to an event. Diff viewer must show old/new SHA and changed path; evidence viewer includes individual test commands, exit codes and log snippets, not model-only narration. Approval button disabled until server reports VERIFIED; frontend guards are not security controls. Confirmation shows target owner/repo/branch and patch digest. SSE reconnection handles duplicate events idempotently. Provide keyboard navigation and text labels for status.

## 11. Implementation dependency graph
`schema -> DB/event ledger -> auth/intake -> repo snapshot -> graph controller -> model gateway -> reproduction agent -> runner -> patch agent -> deterministic verification -> artifact manifest -> UI -> approvals -> GitHub PR -> benchmarking -> submission`. Tavily branches from triage and can be added after baseline reproduction. OTel starts at schema/DB stage, not at the end. Security controls are tested per layer.
