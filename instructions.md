# ReplayForge — Implementation Playbook

**Audience:** developer, Codex agent, engineering lead. **Operating principle:** finish a vertical slice before adding optional agents or connectors.

## 0. Prerequisites, credentials and environment
Develop with Python 3.11 or 3.12, Node.js 22 LTS (validate supported Next.js versions), Git, PostgreSQL 16+ or provider-managed Postgres and an isolated Linux sandbox endpoint. Windows PowerShell is supported for control-plane development. Create isolated `.venv` and lock dependencies using `uv` or Poetry; use `npm ci` for pinned frontend packages. Never commit `.env`, personal OAuth tokens, generated private logs or repositories. Required variables: `DATABASE_URL`, `ARTIFACT_STORE_URL`, `NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, `NEBIUS_MODEL_PRIMARY`, `AUTH_ISSUER`, `AUTH_AUDIENCE`, `SESSION_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_REF`, `PUBLIC_BASE_URL`. Optional: `TAVILY_API_KEY`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `SANDBOX_BACKEND`, `SANDBOX_REMOTE_URL`, `MAX_RUN_COST_USD`, `MAX_ACTIVE_RUNS`. A `.env.example` must contain safe dummy values only. Configure secure secrets service for deployed environment.

## 1. Bootstrap the repo
Create `apps/api`, `apps/web`, `packages/contracts`, `services/orchestrator`, `services/runner`, `infra`, `benchmarks/fixtures`, `tests`, `docs`, `specs`. Initialize a public-ready license (Apache-2.0 or MIT), contributor notes, CODEOWNERS, .gitignore, pre-commit hooks and CI workflow with `pull_request` minimum privileges. API uses routers for run intake, events, artifacts, approval, PR; service layer enforces authorization; ORM models and Alembic migrations persist state. Use dependency injection for every external adapter so unit tests do not require Nebius/Tavily/GitHub credentials.

## 2. Establish live Nebius access (stop/go gate)
Register via Devpost and apply hackathon credits using current program instructions. From a secure local environment, query accessible Token Factory model list and execute one real chat completion using a NVIDIA Nemotron model. Record model ID, endpoint, response shape, HTTP status, latency and billed/returned token metadata when exposed. Reference published base URL `https://api.tokenfactory.us-central1.nebius.com/v1/` and sample `nvidia/nemotron-3-super-120b-a12b`; these are configuration defaults, not guarantees. Do not silently swap to non-NVIDIA models. If no NVIDIA inference access, mark blocked and do not assert hackathon compliance.

## 3. Build common contracts
First implement Pydantic models in `packages/contracts`: `CreateRun`, `RunState`, `ContextBundle`, `Hypothesis`, `ReproductionPlan`, `PatchProposal`, `TestObservation`, `GateResult`, `EvidenceManifest`, `ApprovalRequest`, `PRResult` and versioned event envelopes. Enforce immutable source commit, enum states, result provenance IDs and strongly typed error codes. JSON schema contracts must remain compatible between API, workers and UI. Write unit tests for invalid state transitions and forged gate results before implementing agents.

## 4. Implement secure file ingestion
GitHub fetch adapter downloads approved repository snapshot by immutable commit SHA; refuse `.git` hooks, submodules, symlink escaping, unexpected archives and oversized content. Normalize/parse failure logs and redact tokens. Chunk source context by AST and error stack path; preserve `filename:line` anchors and hashes. Repository content is untrusted; never let agent text turn it into executable command policy.

## 5. Implement durable orchestration
Create a LangGraph graph with typed state and nodes described in `AGENTS.md`. Checkpoint state in Postgres; use jobs with atomic leasing plus heartbeat and recovery. Emit versioned `run_events` in the same transaction as state updates and serve SSE with replay cursor. Implement `CANCELLED` and interruption handling. No state skip to VERIFIED is permitted through API or graph.

## 6. Implement the runner abstraction
Define `SandboxBackend.create`, `.exec_allowlisted`, `.apply_validated_patch`, `.collect_artifacts`, `.terminate`. Build fake adapter for unit tests, then remote isolated implementation for live E2E. Allow command templates such as `pytest -q <preapproved test selector>`, `ruff check .` and known static scans; avoid `shell=True`. Persist base image digest, repo SHA, dependency hashes, command argv, exit status, timing, output digest. Enforce isolation from control-plane network/identity and hard execution limits. Never run untrusted fixtures on the Windows host.

## 7. Wire in Nemotron and Tavily
ModelGateway wraps OpenAI-compatible Token Factory client, performs startup model probe, adds deadlines, per-run budgets, bounded retry, output validation and sanitized token accounting. Triage, test design and patch generation may share one Nemotron model initially. ResearchAdapter uses Tavily API key, bounded `max_results`, sanitized queries and title/URL/snippet citation records. Add a deterministic demo case where documentation search is materially useful, but no external webpage can dictate commands.

## 8. Implement verified repair flow
First seed a known-buggy FastAPI fixture. Ingest issue. Produce regression test and run on original SHA. Require target-specific behavioral failure. Create candidate diff from Nemotron; validate changed paths and patch max size; replay in a fresh sandbox. Run regression plus existing tests; run security/static gate; then repeat critical tests in another clean sandbox. Write `EvidenceManifest` with sha256 and storage references. Map missing or ambiguous evidence to INCONCLUSIVE. Never treat passing pre-existing tests alone as proof that incident is fixed.

## 9. Approval and PR
Frontend review presents exact patch and evidence digests. API requires authenticated reviewer permission, anti-CSRF measure and short-lived one-use approval tied to run/version/commit/target branch/policy. Backend creates dedicated branch under approved GitHub installation and PR, with body containing summary, test evidence, limitations and source/patch IDs. Retry is idempotent. No auto-merge, GitHub workflow modifications or production deploy.

## 10. Build frontend
Use Next.js App Router with accessible, type-safe forms; dashboard, new-run, run detail, evidence tab, compare diff, approval and benchmark results. Use server-side authentication and SSE client with backoff/reconnect; replay from last sequence. Color alone cannot convey status. Never display sensitive raw tokens, confidential repo data or unredacted stdout. Include visible 'INCONCLUSIVE' explanation and 'rejected patch' example in demo.

## 11. Test and harden
Unit: state machine, diff boundaries, source hash verification, secret redaction, budgets, model schema failures. Contract: API schemas and adapter failure shapes. Integration: Postgres lease recovery, SSE replay, storage checksums, patch/test executable cycle, approval rejection. Adversarial: prompt injection inside README, symlink to host secret, path traversal, binary diff, disallowed command, poisoned Tavily snippet, expired approval, duplicate PR retries, replay tampering. E2E: three bounded fixtures (checkout, aggregation, concurrency). Benchmark against hidden holdout mutations; record actual observed values not optimistic placeholders. Security review includes threat model and breach containment.

## 12. Build, run and validate workflow (command templates)
Backend install: `python -m venv .venv` then activate for local shell; `python -m pip install -e '.[dev]'` from API package once `pyproject.toml` exists. Backend local: `uvicorn replayforge_api.main:app --reload --port 8000` **control plane only**. Frontend: `npm ci && npm run dev` within `apps/web`. Tests: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy apps/api services packages` (adjust module paths in working repository), `npm run lint`, `npm run typecheck`, `npm run test:e2e`. Live smoke: `python scripts/check_nebius.py --model "$NEBIUS_MODEL_PRIMARY"` and `python scripts/e2e_fixture.py --fixture checkout --require-live-nebius --require-isolated-runner`; scripts are planned files and must be implemented before these commands can pass. Windows PowerShell environment variable syntax differs; document `.env` setup for Windows separately. Never report the command template as executed until it has been run.

## 13. CI/CD and deployment
CI on PR runs type/lint/unit/contract without credentials and checks dependency licenses/secrets. Protected default branch requires reviews. CD builds pinned digest artifacts, applies migrations via a restricted job, deploys API/UI, and separately deploys worker/runner control with distinct IAM. Production ingress enforces HTTPS, authentication and rate limiting. Readiness includes DB, worker availability and current Nebius status without blocking liveness. Use one revision rollback path and exercise backup restoration. Demo URL must stay working during Dec 1–15 judging.

## 14. Submission routine
By October 27 record a truthful 2:50 demo, showing actual failing baseline, Nemotron runtime, patch, executed clean replay and approval-controlled PR. By Oct 28 publish README, evaluation data and security caveats, OSS license, screenshots, technical architecture and working hosted link. By Oct 29 submit and manually verify visibility of Devpost entry, public YouTube URL, repo and live link. Do not wait until the Oct 30 deadline (10:00 PDT / 22:30 IST). Check rule updates and contest eligibility.

## 15. Recovery / triage playbook
`401/403` Nebius => recheck key scope and model catalog, never fall back to unrelated provider silently. `429` => obey Retry-After with bounded backoff; release run with retryable status if budget exhausted. Runner unavailable => `INCONCLUSIVE` plus incident event, do not fake test outcomes. GitHub 401/403 => store verified evidence but block PR until permissions restored. Test flakiness => repeat on clean runner and label inconclusive if inconsistent. Missing artifact => verification fails closed. Expired reviewer approval => re-review exact patch. Abnormal spending => disable new runs and notify operator. Sensitive data leak => revoke exposed token, quarantine artifact, audit scope, rotate credentials and preserve minimal redacted incident evidence.


## Current runtime guides and delivery tracking (22 October 2026)

Use todo.md's current-focus section to resume work; historical test counts belong
to the commits that produced them. Record new acceptance evidence in
implementation-log.md and update the affected feature contract/README entry point.
The current harness entry point and permission/cost flags are documented in
docs/agent-harness.md; durable recovery/advisory memory limits are in docs/memory.md.
The combined worker currently reaches REPRODUCING. Continue through admitted
execution and trusted report production to close the original M3 exit; do not
substitute scheduler counts or historical lessons for a verified repair.
Authentication is assigned to the user's teammate. Coordinate interfaces while
prioritizing one complete checkout repair, approval/PR flow and submission assets.


## Durable execution/review guides (22 October follow-up)

Use docs/durable-producer.md for measured preparation/receipt recovery and the
producer-to-evidence handoff. Use docs/approval-journal.md for internal human
review/nonce contracts. These do not automatically enable later harness stages
or public approval routes. The authentication owner supplies independent current
human identity/membership; never accept ReviewPermission JSON as proof. Real
GitHub publication, deterministic seven-gate production and deployment remain
tracked in todo.md. Production role grants/migrations require deployment review.

## Publication integration — 2026-10-23

Use docs/github-publication.md for the internal approval-to-publisher contract.
Keep identity loading and installation credentials with the authentication owner.
Never invoke the adapter from model tools or accept ReviewPermission from JSON.
Live writes are opt-in and require the concrete exact reviewed fixture approval;
local synthetic contract success is not M4 acceptance.

## GitHub identity follow-up — 2026-10-23

The user reassigned authentication implementation into this workstream. Follow
`tasks/plan.md` and `docs/github-authentication.md`; the initial separate loopback
OAuth/session foundation does not replace the existing local startup. Trusted
host dependencies supply auth-only keys/settings and an AuthJournal; callback
query access logging must be disabled/redacted. Keep test-origin sessions denied
outside explicit synthetic tests. No live or public setup has been enabled.
