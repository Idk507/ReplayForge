import { z } from "zod";

export const uuid = z.uuid();
export const ref = z.string().regex(/^sha256:[a-f0-9]{64}$/);
export const stages = ["CREATED", "VALIDATING", "QUEUED", "COLLECTING", "TRIAGING", "REPRODUCING", "PATCHING", "VERIFYING", "VERIFIED", "AWAITING_APPROVAL", "PR_CREATING", "PR_OPENED", "PR_FAILED", "CLOSED", "NOT_VERIFIED", "INCONCLUSIVE", "POLICY_BLOCKED", "CANCELLED"] as const;
export const runSchema = z.object({
  run_id: uuid, repository_id: uuid, source_commit: z.string().regex(/^[a-f0-9]{40}$/),
  source_manifest_ref: ref.nullable(),
  current_stage: z.enum(stages), version: z.number().int().nonnegative(), trace_id: uuid,
  evidence_manifest_ref: ref.nullable(), verification_ref: ref.nullable(),
  cancellation_requested: z.boolean(), failure_reason: z.string().nullable(),
  remaining_budget: z.object({model_calls: z.number(), sandbox_commands: z.number(), research_searches: z.number(), execution_seconds: z.number(), estimated_spend_microusd: z.number()}),
});
export type Run = z.infer<typeof runSchema>;
export const sourceManifestSchema = z.object({
  schema_version: z.literal("1.0"), repository_id: uuid, source_commit: z.string().regex(/^[a-f0-9]{40}$/),
  root_tree: z.string().regex(/^[a-f0-9]{40}$/), evidence_origin: z.enum(["TEST_TRANSPORT", "REMOTE_SERVICE"]),
  commit_identity: z.literal("GITHUB_HTTPS_METADATA"), raw_commit_verified: z.literal(false),
  observed_at: z.iso.datetime({offset:true}), trace_id: uuid, policy_version: z.literal("repository-v1"),
  files: z.array(z.object({path:z.string().min(1).max(240),blob_id:z.string().regex(/^[a-f0-9]{40}$/),sha256:z.string().regex(/^[a-f0-9]{64}$/),size_bytes:z.number().int().nonnegative().max(65536)}).strict()).max(128),
}).strict();
export type SourceManifest = z.infer<typeof sourceManifestSchema>;
export const createSchema = z.object({repository_id: uuid, commit_sha: z.string().regex(/^[a-f0-9]{40}$/), incident_text: z.string().min(1).max(20000), idempotency_key: uuid}).strict();
export const eventSchema = z.object({sequence: z.number().int().positive(), event_type: z.string().max(64), stage: z.enum(stages), version: z.number().int().nonnegative(), observed_at: z.iso.datetime({offset: true}), trace_id: uuid});
export type RunEvent = z.infer<typeof eventSchema>;
export const executionStatusSchema = z.object({run_id: uuid, phases: z.array(z.object({phase:z.enum(["BASELINE","CANDIDATE","EXISTING_SUITE","CLEAN_REPLAY"]),status:z.enum(["NOT_PLANNED","PLANNED","SUBMISSION_UNKNOWN","OPERATION_KNOWN","OPERATION_PENDING","PROVIDER_TERMINAL_UNVERIFIED","RECEIPT_RETAINED_UNVERIFIED","EXPIRED","INTEGRITY_FAILURE"]),execution_id:uuid.nullable().optional(),operation_id:uuid.nullable().optional(),plan_ref:ref.nullable().optional(),origin:z.enum(["TEST_TRANSPORT","REMOTE_SERVICE"]).nullable().optional(),observed_at:z.iso.datetime({offset:true}).nullable().optional(),can_authorize_execution:z.literal(false),can_authorize_verification:z.literal(false)}).strict()).max(8)}).strict();
export type ExecutionStatus = z.infer<typeof executionStatusSchema>;
export const gateNames = ["BASELINE_REPRODUCED", "PATCHED_REGRESSION_PASS", "EXISTING_SUITE_PASS", "CLEAN_REPLAY_PASS", "PATCH_SCOPE_PASS", "ARTIFACT_INTEGRITY_PASS", "SECURITY_GATE_PASS", "STATIC_ANALYSIS_PASS"] as const;
export const summarySchema = z.object({verification_ref: ref, verdict: z.object({status: z.enum(["VERIFIED", "NOT_VERIFIED", "INCONCLUSIVE"]), observed_at: z.iso.datetime({offset: true}), can_authorize_publication: z.literal(false), missing: z.array(z.enum(gateNames)).optional(), failed: z.array(z.enum(gateNames)).optional()}), gates: z.array(z.object({gate: z.enum(gateNames), phase: z.enum(["BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY", "PATCH_SCOPE", "ARTIFACT_INTEGRITY", "SECURITY", "STATIC_ANALYSIS"]).optional(), status: z.enum(["PASS", "FAIL", "MISSING"]), execution_ref: ref, execution_id: uuid.nullable().optional(), operation_id: uuid.nullable().optional(), image_uuid: uuid.optional(), image_digest: ref.optional(), configuration_ref: ref.optional(), source_commit: z.string().regex(/^[a-f0-9]{40}$/).optional(), patch_ref: ref.nullable().optional(), workspace_sha256: z.string().regex(/^[a-f0-9]{64}$/).optional()})).max(8)});
export type Summary = z.infer<typeof summarySchema>;
export const manifestSchema = z.object({manifest_id: ref, entries: z.array(z.object({name: z.string().max(81), artifact_id: ref, size: z.number().int().nonnegative()})).max(32)});
export type Manifest = z.infer<typeof manifestSchema>;
export const patchSchema = z.object({proposal: z.object({unified_diff: z.string().max(500000), justification: z.string().max(1000), risk_notes: z.string().max(1000)})});
export type Patch = z.infer<typeof patchSchema>;
const documentationUrl = z.url().refine(value => {
  const url = new URL(value);
  return url.protocol === "https:" && !url.username && !url.password && !url.search && !url.hash &&
    ["docs.python.org", "docs.pytest.org", "fastapi.tiangolo.com", "pandas.pydata.org", "docs.sqlalchemy.org", "www.postgresql.org"].includes(url.hostname);
});
export const researchSchema = z.array(z.object({report: z.object({evidence_origin: z.enum(["NOT_RUN", "TEST_TRANSPORT", "REMOTE_SERVICE"]), sources: z.array(z.object({url: documentationUrl, title: z.string().max(240), snippet: z.string().max(1000), retrieved_at: z.iso.datetime({offset:true}), trust: z.literal("UNTRUSTED_EXTERNAL"), can_authorize_execution: z.literal(false)})).max(5)})})).max(2);
export type Research = z.infer<typeof researchSchema>;

export const evaluationReportSchema = z.object({
  schema_version: z.literal("replayforge.m4-evaluation-report.v2"),
  evidence_authority: z.literal("UNAUTHENTICATED_AGGREGATE"),
  cases: z.array(z.object({
    case_id:z.string(), fixture_id:z.string(), available:z.boolean(),
    unavailable_reason:z.string().nullable(), attempted_cases:z.number().int().nonnegative(),
    live_attempts:z.number().int().nonnegative(), test_transport_attempts:z.number().int().nonnegative(),
    local_static_attempts:z.number().int().nonnegative(), verified_oracle_correct:z.number().int().nonnegative(),
    false_verified_count:z.number().int().nonnegative(), inconclusive_count:z.number().int().nonnegative(),
    failure_categories:z.record(z.string(),z.number().int().nonnegative()),
  }).strict()).max(64),
  attempts:z.array(z.object({
    case_id:z.string(), origin:z.enum(["REMOTE_SERVICE","TEST_TRANSPORT","LOCAL_STATIC"]),
    status:z.enum(["VERIFIED","REJECTED","FAILED","INCONCLUSIVE","BLOCKED"]),
    attempted:z.boolean(), trace_id:uuid.nullable(), reproduction:z.enum(["PASS","FAIL","UNKNOWN"]),
    oracle_status:z.enum(["PASS","FAIL","UNAVAILABLE"]), failure_category:z.string().nullable(),
    evidence_refs:z.array(ref).max(32), model:z.string().nullable(), provider:z.string().nullable(),
    provider_estimated_cost_usd:z.number().nullable(), latency_seconds:z.number().nullable(),
  }).strict()).max(10000),
  totals:z.object({
    available_cases:z.number().int().nonnegative(), unavailable_cases:z.number().int().nonnegative(),
    attempted_cases:z.number().int().nonnegative(), live_attempts:z.number().int().nonnegative(),
    test_transport_attempts:z.number().int().nonnegative(), local_static_attempts:z.number().int().nonnegative(),
    reproduced_baselines:z.number().int().nonnegative(), verified_oracle_correct:z.number().int().nonnegative(),
    false_verified_count:z.number().int().nonnegative(), inconclusive_count:z.number().int().nonnegative(),
    patch_scope_rejections:z.number().int().nonnegative(), reproduction_validity_rate:z.number().nullable(),
    verified_patch_rate:z.number().nullable(), inconclusive_rate:z.number().nullable(),
    total_provider_estimated_cost_usd:z.number().nullable(), median_latency_seconds:z.number().nullable(),
    p95_latency_seconds:z.number().nullable(), latency_sample_count:z.number().int().nonnegative(),
  }).strict(),
}).strict();
export type EvaluationReport = z.infer<typeof evaluationReportSchema>;

/** Finite SSE pages: reject malformed frames and advance only validated IDs. */
export function parseEvents(text: string, after: number): RunEvent[] {
  if (text.length > 131072) throw new Error("EVENT_PAGE_TOO_LARGE");
  const events: RunEvent[] = [];
  let cursor = after;
  for (const frame of text.replaceAll("\r\n", "\n").split("\n\n")) {
    const lines = frame.split("\n");
    const id = lines.find(l => l.startsWith("id: "))?.slice(4);
    const data = lines.find(l => l.startsWith("data: "))?.slice(6);
    if (!id && !data) continue;
    if (!id || !data || !/^[0-9]{1,15}$/.test(id)) throw new Error("EVENT_FRAME_INVALID");
    const event = eventSchema.parse(JSON.parse(data));
    if (event.sequence !== Number(id)) throw new Error("EVENT_ID_MISMATCH");
    if (event.sequence <= after) continue;
    if (event.sequence <= cursor || events.length >= 100) throw new Error("EVENT_ORDER_INVALID");
    events.push(event); cursor = event.sequence;
  }
  return events;
}
