# Authorized source handoff and independent replay inputs

Status: local contract accepted; live Nebius repair execution remains open.

`services.auth.collection.CollectionBroker.materialization_source` is an
internal worker-only path. It uses the existing worker-key/origin boundary,
loads repository paths only from the retained source manifest, checks the
owner's current repository access and execution consent, and rechecks the live
lease/version/stage before fetching. The auth service obtains the provider
credential; it returns only bounded source file bytes and their manifest
identities to the worker. Each returned path, byte length, SHA-256, and Git blob
object ID must match the pinned manifest. The response is neither logged nor
stored in public events.

`services.orchestrator.authorized_materialization` resolves the owner-scoped
manifest and invokes this broker before packaging. Baseline materialization
rebuilds the original source plus the retained reproduction test. Candidate
materialization rebuilds from those same bytes and applies the retained patch
exactly. Clean replay uses a separately selected archive built from the
unchanged original bytes and the same test; it does not reuse the patched
candidate archive. Both candidate and clean phases retain their distinct
durable intents, operation identities, and measurement records, each tied to
the same source, test, and patch reference.

Tests use a disposable PostgreSQL database and in-process HTTP transport. They
verify source retrieval through the authenticated broker, denial after access
revocation, per-file Git/SHA-256 checking, exact candidate versus clean bytes,
and existing one-shot recovery. These tests do not fetch a real user's repo,
execute untrusted tests in Nebius, establish a trusted runner/collector, produce
signed receipts, or generate the seven gates.

## Live M3 acceptance still required

The retained pinned OCI import failed with HTTP 404 (see `docs/confined-runner.md`
and `docs/evidence/pinned-image-import*-observation.json`). No accepted runner
capability/configuration or trusted collector/gate signing key has been
established from that image. No live baseline, candidate or clean replay was
submitted in this slice. Consequently there are no trusted signed receipts,
all-seven-gate result, or verified/rejected repair journey to show as complete.
Do not retry the failed provider import blindly or substitute an image UUID/tag
for pinned provenance. M3 must remain open until a qualified immutable runtime
and independently provisioned collector/gate trust are available and the full
journey is retained and shown by the workbench.

## Local test evidence

- `uv run pytest tests/unit/test_candidate_workspace.py -q`: 10 passed.
- `uv run pytest tests/unit -q`: 812 passed.
- `uv run pytest tests/contract -q`: 227 passed, one upstream Starlette/AnyIO
  deprecation warning.
- Disposable PostgreSQL focused collection/materialization run:
  `docs/evidence/authorized-source-materialization-focused.xml`, 14 passed,
  exit 0. One upstream Starlette/AnyIO deprecation warning.
- From `apps/web`, `npm test -- --grep "synthetic contract: reconnect preserves
  cursor, missing gates stay missing"` and `npm test -- --grep "synthetic
  contract: rejected gate, inert diff and hash-checked artifact preview"`: each
  passed. These exercise visible missing/rejected gate display with synthetic
  API data; they do not supply actual run receipts.
- `uv run ruff check ...`, `uv run ruff format --check ...` and
  `uv run mypy services/runner/candidate_workspace.py
  services/orchestrator/authorized_materialization.py`: passed.
