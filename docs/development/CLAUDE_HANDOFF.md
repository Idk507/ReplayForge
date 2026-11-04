# Claude development handoff

Updated 2026-11-01. Authoritative detail stays in `todo.md`, `milestones.md` and the linked docs; this file is only continuity state.

## Branch and base
- Branch `claude/replayforge-local-product`, based on `ffa8cc7` (public `main`) plus:
  - `7886700` Codex's uncommitted checkpoint imported from `C:\Users\dhanu\Downloads\ReplayForge-public-release-candidate` (that worktree was only read, never modified). Contains: collection-worker recovery (claims only never-claimed jobs), Azure DB worker mode, timeline request-order fix in `apps/web/components/workbench.tsx`, public-auth config, `deploy/` scripts, new tests and 2026-10-30 evidence files. `docs/evidence/*.jsonl` operation journal and `docs/submission/luna-*` prompt files were deliberately not imported.
- Imported from the original checkout (gitignored/absent in the release tree): `artifacts/` (not committed) and `docs/evidence/nebius-pytest-wheelhouse.json`.
- Small independent fix: ruff import ordering in `services/verifier/replay.py`, `tests/unit/test_replay_pair.py`.

## Validated (2026-11-01, Windows, disposable PostgreSQL 18)
| Check | Result |
|---|---|
| Full backend `scripts.test_postgres` (evidence `full-backend-local-20261104a.xml`, commit 308d82f) | **1867 passed, 3 skipped (Docker/public-deployment tests), exit 0**, 15m10s |
| `scripts.test_postgres --web` | 16 Playwright passed incl. real local API test |
| `tests/integration/test_collection_reconciliation.py` | 20 passed (journal, reconcile, lifecycle) |
| ruff check/format, mypy (166 files), unit | clean; 992 passed |
Earlier runs failed (3, then 24) and were fixed: `0036` downgrade conditional, worker schema pin 0037,
order-dependent downgrade tests, Playwright scope. Evidence logs retained under `docs/evidence/`.

## Done this phase
- Migration `0037` + `services/persistence/collection_journal.py`; `dispatch_once` writes intent -> SENT -> outcome.
- Audited operator reconciliation (`scripts/reconcile_collection.py`); see `docs/collection-reconciliation.md`.
- Local lifecycle acceptance (synthetic source): create -> claim -> collect -> manifest -> timeline, read cold.
- Verification worker default schema revision is now 0037.

## Repository index (new, unit-tested only)
- `services/context/index.py`: deterministic `build_index(snapshot)` (roles, languages, frameworks, symbols with
  line ranges, imports, entry points, test-to-source map, canonical digest, per-file blob/sha256 provenance),
  `rank_paths(index, incident)` (path/symbol/import names only; comments and strings cannot steer ranking),
  `search_symbols` (literal substring). 8 unit tests; 1000 unit tests pass; ruff/mypy clean.
- NOT yet wired: collection still selects paths from explicit incident references or a fallback before any
  fetch, and the index is not retained as an artifact. Wiring needs a two-phase acquire (tree identities, rank,
  then fetch <=16 blobs) in `GitHubReader.acquire`/`CollectionBroker.process`, plus artifact storage
  (`Store.context`-style, checksummed) and a UI panel. Run the full DB suite after that change.

## Also done after the index
- Tree-listing path ranking when the incident names no file (`rank_listing`, `GitHubReader.list_paths`).
- `ContextBundle` repository map (index digest, frameworks, symbols of unincluded files, test links).
- Read-only `GET /api/v1/runs/{id}/diagnostics` + workbench panel (WORKER_NOT_CLAIMING, NEEDS_RECONCILIATION, ...).
- Full index retained as checksummed owner-scoped artifact (migration 0038, `Store.repository_index`, GET `/repository-index`, workbench Analysis tab); worker schema pin is now 0038. `scripts.test_postgres --web`: 23 Playwright passed.

## Lease heartbeat (done)
- `LeaseKeeper` renews the worker's live lease during collection/triage/patch calls; see `docs/collection-reconciliation.md`.
- Structured logging and CI workflow done (docs/observability-and-ci.md); CI has not yet run on GitHub.
- Pending from the local audit: tests for `scripts/reconcile_collection.py`, line-ending normalization, stale README counts, bandit hygiene (`usedforsecurity=False`, static query in collection_journal).

## Sandbox fallback (2026-11-04)
- User deleted the Azure deployment; finish the full local workflow first.
- If Nebius sandbox keeps failing, use NVIDIA OpenShell (remote Linux host), then a managed microVM. Plan: `docs/sandbox-fallback-plan.md`. Nothing run against OpenShell yet; external use needs user authorization.

## NOT validated
- No live GitHub/Nebius/sandbox call; collection evidence is `TEST_TRANSPORT`. Nothing is VERIFIED; M3/M4 open.
- `deploy/` tooling and `.ps1` still pinned to 0036 (intentionally refuses 0037; needs review before any deploy).
- Docker-dependent tests skipped (3).

## Next tasks
1. Feed the retained index/ranking into the investigation (triage) and add the synthetic fixture provider; real Nemotron only with explicit authorization.
2. Investigation contract + synthetic fixture provider; real Nemotron only with explicit authorization.
3. Then reproduction/patch/execution (fake adapter only locally), verification, UI diagnostics for reconciliation states.

## Findings
- Run creation already writes a `CREATED` event and a job lease in one transaction (`services/persistence/store.py` ~L552), so an empty production timeline was more likely the UI request-ordering bug (fixed locally, regression-tested) or a stale deployed build — still unproven for run `56f56757`.
- `scripts/run_authenticated_worker.py` already composes collection, triage (opt-in inference) and patch dispatch; local mode is loopback only. The never-reclaim behavior is retained; recovery is now the audited operator reconciliation.

## Decisions
- Did not edit or clean the Codex worktree or the original dirty checkout.
- Fake/synthetic providers must stay labeled and can never yield VERIFIED.
- Azure deployment is out of scope for now.
