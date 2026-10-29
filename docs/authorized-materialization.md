# Authorized source handoff and independent replay inputs

Status: local contract and one live source acquisition accepted; live Nebius repair execution remains open.

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
and clean replay each independently rebuild from those same pinned source
bytes and unchanged test, then apply the exact retained patch. They therefore
contain byte-identical patched inputs; independence comes from separate phase
journals, durable materialization intents, operation identities, measurements,
and fresh compute, not different source archives. `CLEAN_REPLAY` does not reuse
a candidate execution workspace. The candidate-workspace tests first reproduced
the prior defect: clean selected original, unpatched bytes and its workspace
hash differed. The corrected selector now uses the independently rebuilt patched
workspace for both phases.

Tests use a disposable PostgreSQL database and in-process HTTP transport. They
verify source retrieval through the authenticated broker, denial after access
revocation, per-file Git/SHA-256 checking, exact candidate versus clean bytes,
and existing one-shot recovery. These tests do not execute untrusted tests in
Nebius, establish a trusted runner/collector, produce signed receipts, or
generate the seven gates.

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
- Task 1 RED/GREEN: before the fix, `uv run pytest
  tests/unit/test_candidate_workspace.py -q` failed 2 tests / passed 8, proving
  clean replay selected `x=1` while candidate contained patched `x=2` and the
  workspace hashes differed. After the fix, the same command passed 10 tests.
- Disposable PostgreSQL replay/materialization integration:
  `docs/evidence/m3-clean-replay-dispatch-focused.xml`, 24 passed, one upstream
  Starlette/AnyIO deprecation warning, exit 0. The tests confirm distinct
  phase-specific durable operations while preserving patched input identity.
- After Task 1, Ruff check and format check passed; targeted mypy reported no
  issues in the 2 changed source modules. No live/provider execution was
  performed for this correction.

## Live source-acquisition evidence: 2026-10-26

Run `3bbf4636-2b8e-42ab-a645-2a3ef0cfd5a0` connected the explicitly consented
`Idk507/maf_ds_agent` repository at commit
`08d828835fcb0cada16aca45c721ad0075e0c70e`. One bounded invocation of
`uv run --no-env-file python -m scripts.launch_authenticated_worker --run
--env-file .env.worker --max-ticks 1` exited 0 and returned `PROCESSED` at
`TRIAGING`. The committed source manifest is
`sha256:b860474e8c9530ddd0aac327af330b3fd5f26a2e55c92f8ab3d720c430c99c3b`;
its payload checksum and run/repository/commit bindings were independently
checked read-only. It records one remotely fetched `pyproject.toml` (2,117
bytes), SHA-256
`7eb21ee71b02da6069b5b00d84c95984d5a769f9e287efdd478cdcf3a6f52d06`, and Git
blob `73b84e7e8a1603fbd289cd4de7ea82c0c8130e3c`. No source code executed, and
there were zero inference calls, sandbox commands or GitHub writes. Full
evidence is in `docs/evidence/m3-live-source-acquisition.json`.

The Evidence tab now fetches this owner-scoped manifest through the session/local
BFF allowlist and displays only its integrity-checked metadata (path, size, SHA,
Git blob, origin, observed time and root tree), never source bytes. It explicitly
shows that GitHub HTTPS metadata is not a raw Git commit signature. Browser
acceptance: two workbench tests passed (strict bridge path and seven-gate/source
display), the session bridge test passed, and the production build passed. The
seven repair gates remain missing for this source-check run.

This read-only run proves the source-acquisition leg only. It is not a reported
incident, baseline failure, patch, candidate/clean replay, trusted execution
receipt, or verified/rejected repair journey. M3 remains open.
