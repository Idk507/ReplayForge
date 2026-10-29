# Final local demo evidence — 2026-10-29

## Current verification producer acceptance (synthetic)

The final current acceptance is the v5 ES256 durable pipeline run, not the
earlier v4-only demo test below. The controlled disposable PostgreSQL run passed
64 tests (0 failed/errors/skipped), with exact canonical v5 envelope bytes
persisted and independently verified, all seven synthetic gates passing, one
VERIFIED transition, restart/concurrent finalization coverage, and idempotent
repeat finalization. Evidence: `docs/evidence/m3-azure-es256-v5-durable-acceptance-final.xml`.
Focused v5 and gate unit tests: 49 passed. This does not contact Azure or Nebius,
execute checkout source, or provide production trust.

## Four-phase verification test (synthetic)

Command:

```powershell
uv run python -m scripts.test_postgres --pytest-target tests/integration/test_production_verification_loader.py --evidence-name m3-final-demo-four-phase-20261028.xml --timeout-seconds 600
```

Result: **27 passed**, exit 0, disposable PostgreSQL 18.6. The positive case
used synthetic test-only identities and persisted all four phase records:

| Phase | Execution ID | Provider operation ID |
|---|---|---|
| BASELINE | `c7eab4c8-bdf3-4bfb-b02e-16b793b9d922` | `6d39f076-af23-4f25-9540-3200e7d9ac82` |
| CANDIDATE | `6a5f3b24-3c1d-415b-80cd-fa369d84c73e` | `56e5237d-63da-4d50-85eb-c621743e4adb` |
| EXISTING_SUITE | `74da5c62-eb9e-4648-84c3-7283ecbeae9f` | `cb673348-8d09-448d-b7c4-2c362d3ae2af` |
| CLEAN_REPLAY | `81b4fa42-cf5d-4041-9d1d-1bc7dd25fc78` | `0bb7837b-566a-4678-9f63-af917db0eca4` |

The seven test-derived gates passed: `BASELINE_REPRODUCED`,
`PATCHED_REGRESSION_PASS`, `EXISTING_SUITE_PASS`, `CLEAN_REPLAY_PASS`,
`PATCH_SCOPE_PASS`, `ARTIFACT_INTEGRITY_PASS`, and `SECURITY_GATE_PASS`.
The synthetic v4 envelope is
`sha256:e02873ad16d3056658df148e51c80ce95830ee281de6beccef217a77f90272b3`;
independent signature validation passed, the disposable run transitioned to
VERIFIED once, repeat finalization returned the same envelope/version, worker
restart recovered, and concurrent finalizers yielded one finalization and one
idle worker. Eighteen adversarial variants were rejected without persisting a
false passing envelope. Full JUnit evidence:
`docs/evidence/m3-final-demo-four-phase-20261028.xml`.

This integration test uses the production journal, producer and worker
components, but every identity, receipt, assessor payload and operation is
synthetic. It does not run the checkout fixture or contact Nebius.

## Local API and workbench

Command:

```powershell
uv run python -m scripts.test_postgres --web --timeout-seconds 600
```

Result: **14 Playwright tests passed**, exit 0. A disposable API/database and
Next.js process supported the actual local run intake, persisted status, cancel,
reload and gate/missing-evidence display. Other browser routes use explicit
synthetic responses. The new `/evaluation` route displayed 1 available fixture,
3 unavailable cases, and zero attempts, with an unauthenticated label. JUnit:
`docs/evidence/m4-final-api-browser-20261028.xml`. Captures:
`m4-evaluation-status-desktop.png`, `m4-evaluation-status-mobile.png`,
`m4-disposable-workbench-desktop.png`, and
`m4-disposable-workbench-mobile.png`.

The exact review panel used mocked API data: two browser tests passed for exact
source/patch/target binding and tampered-patch denial. Mocked GitHub lost-response
discovery passed one contract test without making a GitHub write. Evidence:
`m4-final-review-browser-20261028.xml` and
`m4-final-mocked-github-idempotency.xml`.

## Evaluation panel responsive acceptance

Visual review found the original mobile screenshot clipped the right side of the
wide inventory table. The panel now renders the rows as labeled cards on narrow
screens. `npm test -- tests/evaluation.spec.ts` passed 2/2 after a fresh
production build; the refreshed 390px capture shows the complete rows and keeps
the unauthenticated/zero-attempt labels visible. Evidence:
`docs/evidence/m4-evaluation-responsive-20261029.xml`,
`docs/evidence/m4-evaluation-status-mobile.png`, and
`docs/evidence/m4-evaluation-status-desktop.png`. The second test row is a
synthetic `TEST_TRANSPORT` display fixture only.

## How to present

1. Run `uv run python -m scripts.test_postgres --pytest-target tests/integration/test_production_verification_loader.py --pytest-target tests/integration/test_verification_journal.py --evidence-name m3-azure-es256-v5-durable-acceptance-final.xml --timeout-seconds 1200`; show the passing JUnit properties and call it a synthetic controlled verification test. The retained final run already reports 64 passed; avoid rerunning unless a fresh run is needed for recording.
2. Run `uv run python -m scripts.test_postgres --web --timeout-seconds 600`; show the local intake/status/reload and gate/missing-evidence browser acceptance. It exits after running the suite.
3. Start the static local panel separately from `apps/web` using `npm run start` after the documented `npm run build`; open `http://127.0.0.1:3000/evaluation`. This page needs no API and makes no provider calls.
4. Show `/evaluation` and say no benchmark fixture has been executed; do not quote a rate.
5. Show the exact-review/mock-publication test artifacts, noting that no real PR was created.

The demo is not one continuous live repair. It is a truthful presentation of
separately retained historical fixture evidence, current synthetic verification
acceptance, local UI acceptance, and current evaluation exclusions.

## Final submission preflight — 2026-10-29

Fresh local commands and results:

| Command | Result |
|---|---|
| `uv run python -m scripts.check_checkout` | `M2_FIXTURE_ACCEPTED`, no failed static checks; both execution/verification authorization flags false; no checkout code executed |
| `uv run python scripts/report_m4_evaluation.py` | 1 available fixture, 3 unavailable, 0 attempted; no rates |
| `npm run lint` | passed |
| `npm run typecheck` | passed |
| `npm run build` | passed; `/evaluation` included in the build |
| `npm test -- tests/evaluation.spec.ts` | 2 passed, including mobile no-overflow and evidence-label checks |
| `uv run python -m scripts.test_postgres --web --evidence-name m5-final-api-workbench-20261029.xml --timeout-seconds 900` | 15 Playwright checks passed against a task-owned disposable PostgreSQL database and loopback API; database/roles and services were cleaned up |
| `git diff --check` | exit 0; Git printed only existing LF/CRLF normalization warnings |
| `uv run alembic heads` | `0036 (head)`; no intended-database migration was run |

The 15-test JUnit was retained as
`docs/evidence/m5-final-api-workbench-20261029.xml` (SHA256
`34be4b3a2d08ad99fe2d3100ac9a270b12a8c240fbf58c209b781c2d8addfc65`). It
includes a real disposable-database local API create/status/cancel/reload path,
evidence/missing-gate display, and synthetic browser fixtures. It does not run
checkout code or call a provider. The evaluation inventory remained 1 available,
3 unavailable and 0 attempts; all measured rates and costs are null.

The provider config was validated offline without displaying the key. The
configured model is `nvidia/nemotron-3-super-120b-a12b` at the Token Factory
US Central endpoint. `scripts.check_nebius` was not run with `--live`; no new
inference was sent. The exact fixed-request plan and scoped approval boundary
are in `docs/submission/nebius-inference-preflight.md`.

Read-only GitHub connector checks on 2026-10-29 found
`https://github.com/Idk507/ReplayForge` public with default branch `main` and a
readable README; fetching root `LICENSE` returned 404. A read-only GitHub tree
listing showed `.env.example` only and no PEM/private-key filename. The local source tree is
on `codex/m2-checkout-closure`, commit
`977197bdef3f896a5f53295e8fc6b9a09db97cf5`, and dirty; these changes were not
published. `.env` and the local PEM private-key file are ignored by Git and are
not tracked. A fresh exact-value scan of 1,152 tracked and nonignored files
against sensitive values from the protected `.env` and process environment
found 0 matches; values were not emitted. Evidence:
`docs/evidence/m5-secret-exposure-final-20261029.json`. This does not replace a
full entropy/pattern scanner; no dedicated Gitleaks executable was installed.
No public deployment, repository write, video upload, or Devpost submission occurred. The owner approved Apache-2.0 on 2026-10-29; the local curated release candidate now contains the official root `LICENSE` and `NOTICE`. Public GitHub `main` remains unchanged.

The timed 2:45 recording script is `docs/submission/demo-narration.md`;
copy-ready text is `docs/submission/devpost-answers.md`, and license status is recorded in `docs/submission/license-selection-guide.md`. Submission remains blocked on a public demo/test-build URL, authorized publication of the reviewed source revision, public video, and owner-completed feedback/fields.

## Public release artifact boundary

New JUnit, JSON, screenshot, and operational reports under `docs/evidence/`
remain retained in the local workspace and are not added to the release
candidate. Older historical files already present on public `main` remain
unchanged. The new reports include generated test/operation metadata and are
not required to build or run the public source. The candidate carries the
reproducible test code and commands plus the sanitized unauthenticated
evaluation inventory at `apps/web/public/evidence/m4-evaluation-status.json`.
No credential-bearing logs, `.env`, PEM, or local trust document is added.
