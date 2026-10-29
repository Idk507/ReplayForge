# Devpost submission draft — ReplayForge

Status: **draft only; not submitted**. This text describes the current local
working tree and must be reconciled with the exact public commit before use.
The current public repository's default branch is `main`; this working tree is
on `codex/m2-checkout-closure` at `977197bdef3f896a5f53295e8fc6b9a09db97cf5`
with uncommitted changes. No push or deployment was performed.

## Project name

ReplayForge — Evidence-backed repair for checkout incidents

## Track

Coding and Agentic Engineering

## Short description

ReplayForge turns a consented, commit-pinned software incident into a reviewable
repair trail. It records baseline, patch, regression, existing-suite and clean
replay evidence, and derives verification gates from authenticated artifacts
instead of trusting a model's claim that a change works.

## Project description

ReplayForge is designed to make an AI-assisted code repair prove itself. An
operator authorizes a repository and exact source commit; the system records
the incident, durable work stages and evidence; a constrained repair proposal
is checked against the approved paths; and independent executions feed a
seven-gate verifier. A signed result and a one-use, human-reviewed publication
boundary are intended to keep model output separate from verified evidence and
external writes.

The workbench and API run locally. Current local acceptance includes a
four-phase synthetic PostgreSQL run that derives seven passing gates, persists
and independently verifies a signed test envelope, and rejects adversarial
evidence. A separate historical fixed-fixture report records remote baseline,
candidate and clean-replay observations and the Nemotron model ID. These are
distinct evidence sets. The current generalized checkout workflow has not
completed a qualified live sandbox run, trusted live receipts or all seven live
gates, so ReplayForge does not claim a live verified checkout repair.

The evaluation inventory has one available fixture, three unavailable cases
and zero attempts. There is no benchmark success rate. No public demo host or
video is ready yet; the local page at `http://127.0.0.1:3000/evaluation` is not
a public judging URL.

## What is technically new

The central idea is a durable, phase-bound evidence pipeline. Plans, provider
operations, receipts, images, runtime configuration, workspace digests,
capability assessments and artifacts are correlated across baseline,
candidate, existing-suite and clean-replay phases. The verifier derives each
gate from those records, uses an externally injected signer, and rechecks the
persisted envelope. Consent, leases, budgets and uncertain-operation recovery
are kept in the control path. Synthetic acceptance demonstrates local behavior;
it is not evidence that a production signer or sandbox has been provisioned.

## Nebius and NVIDIA use

The configured application client targets Nebius Token Factory's
OpenAI-compatible API with `nvidia/nemotron-3-super-120b-a12b`. Retained
historical evidence records a successful fixed synthetic Token Factory probe
on 2026-10-15 (HTTP 200, 22 input tokens, 27 output tokens, 1.672 seconds) and
a separate fixed checkout fixture record on 2026-10-19 that names the same
Nemotron model and records remote baseline/candidate/clean-replay operations.
Those historical records do not describe the current synthetic v5 acceptance
as a live run. No new inference request was made during this submission
preflight; one fixed non-sensitive inference request is prepared separately and
awaits its scoped approval.

No qualified Nebius sandbox runtime was produced. The earlier image-import
attempt was accepted and later failed while pulling the image with HTTP 404;
the pytest-enabled retained image was never prepared. Do not list Nebius AI
Cloud sandbox execution as a successful part of the demo.

NVIDIA describes Nemotron 3 Super as an open model and publishes its weights
under the NVIDIA Nemotron Open Model License. Review the model-specific terms
and attribute the model accurately; this is a separate license from the
project's Apache-2.0 license. References:
[NVIDIA NGC model record](https://catalog.ngc.nvidia.com/orgs/nim/nvidia/models/nemotron-3-super-120b-a12b/-)
and [NVIDIA Research release](https://research.nvidia.com/labs/nemotron/Nemotron-3-Super/).

## Significant changes during the submission period

The current local tree contains substantial work on the incident/API and
workbench flow, repository consent and source provenance, durable worker and
operation recovery, constrained patch admission, four-phase verification,
signed evidence, reviewer approval, publication controls and evaluation
reporting. The specific acceptance records are linked from `todo.md` and
`docs/submission/final-demo-evidence.md`. The remote `main` branch has not been
updated with this dirty local tree; describe only changes present in the exact
revision ultimately made public.

## Tool feedback fields — owner review required

Use only feedback that reflects the operator's own experience. The following
are factual prompts, not invented testimonials:

- **Token Factory behavior:** The retained 2026-10-15 probe returned HTTP 200
  for the configured Nemotron model with the recorded latency and token counts.
  The operator should add their own assessment of setup and response quality.
- **What needs work:** The image-import 404 occurred in the separate sandbox
  image pull path; it should not be attributed to Token Factory inference.
  Describe that onboarding issue only if it matches the operator's experience.
- **Would use again:** `[Owner: answer from personal experience.]`
- **Nemotron feedback:** `[Owner: describe the retained model response and
  quality; distinguish the model proposal from verifier results.]`
- **Nebius AI Cloud:** No successful qualified sandbox execution is evidenced.
- **Tavily or other NVIDIA tools:** Mention only a relevant retained call and
  actual owner feedback.

## Required submission URLs

- **Public code repository:** https://github.com/Idk507/ReplayForge
- **Public demo/test build:** `[Not available; local loopback does not meet the
  public URL requirement.]`
- **YouTube demo under three minutes:** `[Record, publish, and insert URL.]`

The public repository was read-only checked on 2026-10-29 and is public, but its current main still returns 404 for LICENSE. The owner has now approved Apache-2.0 and the curated local release branch contains the official license text, NOTICE, README identification, and package metadata. The licensed revision is not public until a separate push is authorized. A public demo, video, and remaining owner-reviewed Devpost fields are also outstanding.

### Release comparison snapshot — 2026-10-29 (license follow-up)

Public `main` at `6404fd6c0c3a5df3973238fdecc5ca90d9a5a112` remains unchanged and has no detected license. The curated local release branch was based on that public revision and contains the previously reviewed implementation. Commit `7eff51d758aee783c1c527844d94849abaea8bb9` is immutable and does not include the owner-approved license; a local follow-up commit adds root `LICENSE`, `NOTICE`, README/package metadata, and corrected submission status. The follow-up is not pushed. The candidate continues to exclude private operational reports, credentials, PEM files, the copied checkout fixture, mockup imagery, generated bytecode, and bulky test artifacts.
