# Devpost final answers — copy/paste ready

Items in `[OWNER]` brackets must be completed or approved by the operator.

**Project name:** ReplayForge

**Track:** Coding and Agentic Engineering

**One-line summary:** ReplayForge turns a consented, commit-pinned software incident into a repair trail with independently checkable evidence, instead of trusting an AI's claim that a patch works.

**Problem:** AI tools can produce plausible code fixes, but a plausible patch is not proof the incident is resolved. Teams need to know the failure was reproduced, the fix passes, nothing else broke, and a human approved exactly what is published.

**Solution overview:** An engineer signs in with GitHub, selects an authorized repository, and consents to a pinned commit. ReplayForge records the incident as a durable investigation. The designed pipeline collects source, reproduces the baseline failure, diagnoses with NVIDIA Nemotron via Nebius Token Factory, proposes a constrained patch, then runs regression, existing-suite and clean-replay executions. Seven deterministic gates (BASELINE_REPRODUCED, PATCHED_REGRESSION_PASS, EXISTING_SUITE_PASS, CLEAN_REPLAY_PASS, PATCH_SCOPE_PASS, ARTIFACT_INTEGRITY_PASS, SECURITY_GATE_PASS) derive a verdict from retained evidence. A human must approve the exact patch hash before any draft pull request.

**Architecture:** Next.js workbench (Azure Container Apps, Central India) behind NGINX; FastAPI/Python API; PostgreSQL 18 (private Azure Flexible Server) with an append-only event ledger; GitHub OAuth plus GitHub App installation-based repository authorization; Nebius Token Factory for inference; Nebius sandbox API for remote execution (untrusted code never runs on the API host); Ed25519 v4 and Azure ES256 v5 signed verification envelopes; human approval gate and one-use publication intent.

**NVIDIA / Nebius technologies:** NVIDIA `nvidia/nemotron-3-super-120b-a12b` served through Nebius Token Factory (OpenAI-compatible API). Retained evidence: fixed synthetic probe on 2026-10-15 (HTTP 200, 22 input / 27 output tokens) and a fixed-fixture checkout run on 2026-10-19 naming the same model. Nebius sandbox API was used for the fixture's remote baseline/candidate/clean-replay operations; a later derived-image import failed with a provider pull HTTP 404, so no qualified sandbox runtime exists for the current workflow. Nemotron is under the NVIDIA Nemotron Open Model License, separate from the project's Apache-2.0 license.

**Technical innovations:** Phase-bound durable evidence: plans, provider operations, receipts, images, runtime configuration and workspace digests are correlated across baseline, candidate, existing-suite and clean-replay phases. The verifier derives gates from those records, not from agent claims, and fails closed when evidence or trust is missing. Consent, lease fences, budgets and uncertain-operation recovery (no blind retries of paid or external operations) sit in the control path. Human approval is bound to the exact patch SHA256 and source commit.

**Security and verification architecture:** Source, logs, tests and search snippets are untrusted data; model output cannot alter gate policy; patches are scope- and size-limited and cannot weaken tests; repository code runs only in a remote sandbox; the approval gate is deterministic code, not an LLM; the publisher never force-pushes or merges. Local security tests cover prompt injection, SSRF, path traversal, malicious patches and runner confinement (M4 security suite, 280 tests, local). Anonymous requests to API paths on the public app return 404.

**Challenges:** Establishing trust for remote execution receipts (a provider operation is not a trusted receipt); the Nebius image import failing at registry pull (HTTP 404); keeping multi-phase operations recoverable after worker death without repeating paid calls; deploying the authenticated app on Azure with a private database and a worker that must claim runs.

**What was actually accomplished:**
- Public Next.js app on Azure with working GitHub login (identity Idk507), session persistence, repository discovery, selected-repository authorization, explicit pinned-commit consent, and investigation intake.
- Four-phase durable verification pipeline with signed envelopes (Ed25519 v4; Azure ES256 v5 additive profile); the v5 profile passed 64 disposable-PostgreSQL integration tests and 49 unit tests locally using synthetic identities.
- 15 workbench Playwright tests on a disposable database; evaluation UI; mocked-transport approval-to-PR tests.
- Historical fixed-fixture record of a Nemotron-assisted repair: baseline failed (exit 1), candidate passed (exit 0), clean replay recorded, on a separate synthetic commit.
- Public Apache-2.0 repository.

**Known limitations (disclosed):** We have NOT demonstrated a complete live checkout repair. In the deployed app the investigation was observed stalled at CREATED with no events while the worker path was being repaired `[OWNER: update this sentence if the worker is later fixed and evidence retained]`. Seven-gate results are synthetic; production Azure signing/trust is not provisioned; no real GitHub PR has been created; the evaluation has 1 fixture available, 3 unavailable and 0 attempts, so there is no measured success rate. M3 and M4 are open.

**Future improvements:** Restore the worker claim/collection path in production; qualify a sandbox image with verified OCI identity; run the four trusted phases on the pinned checkout commit; provision production v5 signing trust; complete live approval-to-draft-PR acceptance; execute the benchmark fixtures.

**Significant development updates during the submission period:** Durable incident/event database and workbench; GitHub OAuth, App-installation repository authorization and exact-commit consent; source collection with live-read acceptance; durable worker, lease, budget and uncertain-operation recovery; constrained patch admission; four-phase verification with signed envelopes (v4, then additive Azure ES256 v5); human review and one-use draft-PR publication controls; evaluation reporting UI; Azure Container Apps deployment with private PostgreSQL.

**Tool feedback (owner-authored; do not submit unreviewed):**
- Nebius Token Factory: factual basis — the retained probe returned HTTP 200 for the Nemotron model in 1.672 s. `[OWNER: add your own assessment of setup and response quality.]`
- What needs work: the sandbox image import was accepted and then failed at registry pull with HTTP 404 (this is the sandbox path, not inference). `[OWNER: confirm this matches your experience.]`
- NVIDIA Nemotron: `[OWNER: describe the retained repair call and observed quality; model output is separate from verifier results.]`
- Would use again: `[OWNER]`
- Tavily: only mention if you confirm the retained call is relevant.

**Links:**
- Public repository: https://github.com/Idk507/ReplayForge (Apache-2.0)
- Primary application / test build: https://replayforge-web.blacksky-3aa3a460.centralindia.azurecontainerapps.io/ (GitHub login required for the workbench; `/evaluation` is public)
- Supplementary static evaluation snapshot (not the product): https://agreeable-flower-0846a9300.4.azurestaticapps.net/
- Demo fixture: https://github.com/Idk507/replayforge_checkout_poc
- Video: `[PENDING — insert public YouTube URL once recorded and verified under 3:00]`

**Built with:** Next.js, TypeScript, Python, FastAPI, PostgreSQL, NGINX, Azure Container Apps, Azure Database for PostgreSQL, GitHub OAuth/App, Nebius Token Factory, NVIDIA Nemotron.
