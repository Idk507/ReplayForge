# Submission limitations and security boundaries — 2026-10-28

## October 29 update

Local verification has since advanced to the separate Azure ES256 v5 profile:
64 focused disposable PostgreSQL integration tests and 49 focused unit tests
passed, and local Alembic head is now `0036`. A fresh restore of the protected
backup was migrated from `0031` to `0036` as the intended migration-owner role;
the intended database itself remains at `0031`. The production startup trust
document still only loads v4; no production v5 trust, Azure worker host/identity,
or reachable managed database has been provisioned. Detailed status is in
`docs/evidence/m3-checkout-end-to-end-acceptance.md` and
`docs/evidence/azure-v5-production-operator-configuration.md`.

The latest local workbench lint, typecheck, and production build passed. Current
evaluation remains 1 available / 3 unavailable / 0 attempted, with all rates
null. Screenshots retained under `docs/evidence/` have validated desktop/mobile
PNG dimensions. The demo is local and separated historical, synthetic, and
static evidence; it is not one live checkout repair.

## Acceptance status

- **M3:** Open. The production verification pipeline passes synthetic disposable-PostgreSQL acceptance, but production signer/collector/assessor identities and worker DB role are not provisioned; intended database remains at 0031 while reviewed local head is 0036; no derived pytest image or qualified runtime exists; the target checkout has not completed the four trusted phases and seven live gates.
- **M4:** Open. Exact human-review and publication code has local tests, and mocked GitHub transport proves lost-response discovery. No actual PR is created for this checkout POC. The benchmark inventory has zero attempts, three unavailable cases, and no hidden oracle run. Evaluation results are labeled unauthenticated; they cannot authorize gates. Report v2 retains trace/cost/error metadata if supplied by future attempts, but none is currently present.
- **M5/submission:** Not ready to submit until the owner supplies a public demo URL, selects and adds the required license, records and publishes the video, and completes the Devpost fields/tool feedback.

## Evidence categories

- **Synthetic local:** disposable PostgreSQL rows, test-only signing identities, fake HTTP and Playwright route data. These verify local behavior only.
- **Historical remote fixture:** `docs/evidence/m2-checkout-e2e/report.json` records the fixed checkout fixture's baseline, candidate and clean-replay remote observations and Nemotron model ID. This is a separate historical fixture acceptance; its own report says it does not authorize production verification and does not prove the current generalized workflow.
- **Current live checkout M3:** none. Do not say the current POC has a live Nebius repair, trusted seven-gate verdict, or production `VERIFIED` run.
- **Benchmark:** one fixture is present but unexecuted; ETL, concurrency and approved hidden variation are absent. There are zero eligible runs, so rates remain null.

## Boundaries

- Repository code is not run on the Windows API host. Remote execution must wait for an image whose provenance, confinement and independent receipt trust are qualified.
- Production verification fails closed without the externally provisioned signer and independent verifier/collector/assessor roots. Test signing keys remain test-only.
- Consent, approved paths, pinned commits, lease fences, budgets and durable uncertain-operation guards remain required.
- A retained provider operation is not a trusted receipt or a verification gate. A displayed patch is inert text and still requires human review.
- The browser approval/PR demonstration uses mocked transport; it does not create a GitHub PR. The checkout POC expressly does not request one.
- No intended application database migration, Nebius image preparation/diagnostic, paid inference, public deployment, or GitHub write was performed for this submission preparation.
- GitHub `main` at the checked public SHA still has no selected license. The owner approved Apache-2.0 on 2026-10-29; the curated local release branch now includes the official `LICENSE`, `NOTICE`, README identification, and package metadata in a follow-up to `7eff51d758aee783c1c527844d94849abaea8bb9`. That follow-up is not pushed, so the public requirement remains unmet.


## Apache-2.0 owner approval — 2026-10-29

The owner approved Apache-2.0 for original ReplayForge source code. The local curated candidate contains the unmodified official text and a separate attribution NOTICE. No GitHub write or publication occurred; the public branch remains unchanged. Third-party license obligations remain separate, and a full SBOM review is outstanding.
