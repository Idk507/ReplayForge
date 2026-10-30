# Demo evidence summary — what is real, tested, or incomplete

| Capability | Status | Evidence |
|---|---|---|
| Public Next.js app (Azure Container Apps, Central India) | **Live, verified 2026-10-30:** `/` 200 (renders "Checking your session…" then login), `/evaluation` 200, `/settings/integrations` 200. `/api/runs`, `/api/session`, `/api/auth/session`, `/api/health`, `/runs` return 404 to anonymous requests. CSP, `X-Frame-Options: DENY`, `nosniff`, no-store headers present. | curl checks |
| GitHub login as `Idk507`, session persistence, repo discovery, selected-repo authorization, consent, intake | **Observed live by the operator** (screenshots). Earlier repo-retained login captures: `docs/evidence/auth-live-login.png`. | operator + repo |
| Investigation after intake | **Stalled at CREATED, 0 events** (`56f56757`), pending Codex worker repair | operator screenshot |
| Live source collection | One earlier live fetch reached TRIAGING (2026-10-26): not a reproduction, no gates | `docs/evidence/m3-live-source-acquisition.json` |
| Nemotron via Nebius Token Factory | Historical: probe 2026-10-15 HTTP 200; fixture run 2026-10-19 names the model | `m0-live-inference-attempt.json`, `m2-checkout-e2e/report.json` |
| Fixture baseline fail → patch → pass → clean replay | Historical, different commit (`64acd340…`), standalone, not VERIFIED | `m2-checkout-e2e/report.json` (baseline exit 1, candidate exit 0) |
| Four-phase verification, 7 gates, signed envelope | **Synthetic**, disposable PostgreSQL: 64 passed (v5), 27 passed (v4) | `m3-azure-es256-v5-durable-acceptance-final.xml` |
| Adversarial rejection (prompt injection, SSRF, traversal, malicious patches, runner confinement; 18 forged-evidence variants) | Local tests; M4 security suite 280 passed (as recorded earlier) | evidence XMLs |
| Workbench browser tests | 15 Playwright passed on a disposable DB | `m5-final-api-workbench-20261029.xml` |
| Evaluation inventory | 1 available / 3 unavailable / 0 attempts; rates null | `apps/web/public/evidence/m4-evaluation-status.json` |
| Approval → PR | Mocked transport only; no real PR | `m4-final-mocked-github-idempotency.xml` |
| Public repo + license | `Idk507/ReplayForge` public, default branch `main` at `ffa8cc7`, SPDX Apache-2.0 (GitHub API), raw `LICENSE` 200 | API check 2026-10-30 |
| Static evaluation snapshot | 200; read-only, not the product | curl |

Not independently re-run in this pass: the 280-test security suite and the 64-test v5 suite (figures come from retained reports and the operator briefing).
