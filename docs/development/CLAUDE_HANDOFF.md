# Claude development handoff

Updated 2026-10-30. Authoritative detail stays in `todo.md`, `milestones.md` and the linked docs; this file is only continuity state.

## Branch and base
- Branch `claude/replayforge-local-product`, based on `ffa8cc7` (public `main`) plus:
  - `7886700` Codex's uncommitted checkpoint imported from `C:\Users\dhanu\Downloads\ReplayForge-public-release-candidate` (that worktree was only read, never modified). Contains: collection-worker recovery (claims only never-claimed jobs), Azure DB worker mode, timeline request-order fix in `apps/web/components/workbench.tsx`, public-auth config, `deploy/` scripts, new tests and 2026-10-30 evidence files. `docs/evidence/*.jsonl` operation journal and `docs/submission/luna-*` prompt files were deliberately not imported.
- Imported from the original checkout (gitignored/absent in the release tree): `artifacts/` (not committed) and `docs/evidence/nebius-pytest-wheelhouse.json`.
- Small independent fix: ruff import ordering in `services/verifier/replay.py`, `tests/unit/test_replay_pair.py`.

## Validated here (actual runs, Windows, `uv sync --locked`)
| Check | Result |
|---|---|
| `uv run pytest tests/unit` | 992 passed |
| `uv run ruff check .` / `ruff format --check .` | clean after import-order fix / 337 formatted |
| `uv run mypy` | no issues in 164 files |
| `npm run lint`, `typecheck`, `build` (apps/web) | pass |
| `npx playwright test tests/workbench.spec.ts` | 13 passed, 1 skipped (the real-API test needs the disposable DB) |

## NOT validated (blocked on operator)
- Every PostgreSQL integration test, including the 47 collection worker/broker tests and the skipped real-local-API browser test. `scripts/test_postgres.py` prompts for the local PostgreSQL administrator password (service `postgresql-x64-18` is running). Agents must not be given that password; run from your own terminal, from this worktree:
  - `uv run python -m scripts.test_postgres --pytest-target tests/integration/test_collection_broker.py --evidence-name collection-broker-local-20261030.xml`
  - `uv run python -m scripts.test_postgres --web --evidence-name web-local-20261030.xml --timeout-seconds 900`
- No live GitHub, Nebius or sandbox call was made. Nothing here is VERIFIED, no real PR, and M3/M4 are unchanged (open).

## Findings
- Run creation already writes a `CREATED` event and a job lease in one transaction (`services/persistence/store.py` ~L552), so an empty production timeline was more likely the UI request-ordering bug (fixed locally, regression-tested) or a stale deployed build — still unproven for run `56f56757`.
- `scripts/run_authenticated_worker.py` already composes collection, triage (opt-in inference) and patch dispatch; local mode is loopback only. The "never repeat an unknown external call" behavior must stay until a journaled reconciliation exists.

## Next tasks (in order)
1. Operator runs the two DB commands above; fix any failures.
2. Design the explicit reconciliation workflow for previously claimed collection jobs (states: never started / claim acquired / dispatched / outcome unknown / completed / failed), using existing journal contracts; add disposable-DB tests incl. crash-after-dispatch.
3. Local E2E acceptance: create run → worker (synthetic source transport) → manifest → timeline after reload.
4. Then repository context builder → investigation (fixture provider first) → repair → execution adapter (fake only locally) → verification → frontend polish → evaluation.

## Decisions
- Did not edit or clean the Codex worktree or the original dirty checkout.
- Fake/synthetic providers must stay labeled and can never yield VERIFIED.
- Azure deployment is out of scope for now.
