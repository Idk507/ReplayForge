# Structured logging and CI

## Logging (`packages/observability/logs.py`)
- One JSON object per line on stderr, enabled explicitly (`configure()`); importing the module
  performs no I/O. `scripts/run_authenticated_worker.py --run` configures it; stdout keeps its
  per-tick result objects.
- Closed field set: `user_id, repository_id, installation_id, run_id, job_id, operation_id,
  worker_id, source_commit, stage, attempt, status, duration_ms, error_class, trace_id, action,
  operator`. Anything else, any value that looks like a credential, and any string outside a
  conservative character set is **dropped** and named in `dropped_fields`. Event names must be
  code-constant identifiers. Exceptions are logged by class name only (no message/traceback).
- Never logged: lease tokens, worker/session/API keys, prompts, source text, incident text,
  operator reasons (those stay in the audit table).
- Events: `collection.intent|sent|dispatch.ok|dispatch.error|outcome`, `triage.dispatch.*`,
  `patch.dispatch.*`, `lease.lost`, `lease.renewal_degraded`, `reconcile.decision|blocked`,
  `worker.start|stop`. One run is traceable by `run_id`/`operation_id`/`trace_id`.
- Local JSON traces only; no telemetry backend, OpenTelemetry export or dashboard is implemented.
  Token/cost accounting stays in the existing journals, not in logs.
- Tests: `tests/unit/test_observability_logs.py` (8), `tests/integration/test_structured_logging.py` (3).

## CI (`.github/workflows/ci.yml`)
Read-only token, no secrets, no deployment, provider or GitHub-write calls; actions pinned to commit SHAs.
- `python-quality`: `uv sync --locked`, ruff check/format, mypy, hash-verified pytest wheelhouse
  (`scripts/fetch_pytest_wheelhouse.py --run`), unit+contract tests, `scripts/check_secrets.py`, pip-audit.
- `postgres-integration`: Postgres 18 service container; `scripts.test_postgres` with
  `REPLAYFORGE_TEST_ADMIN_PASSWORD` (throwaway; interactive prompt otherwise) runs the full suite on a
  disposable database and roles; JUnit uploaded.
- `web`: npm ci, lint, typecheck, build, `npm audit --omit=dev`, Playwright (chromium via
  `PLAYWRIGHT_CHANNEL`).
Status: the workflow passed `actionlint` and every step was run locally, but it has **not yet run on
GitHub**; treat the first Actions run as unverified. Docker-dependent tests still skip.
