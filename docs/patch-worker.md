# Admitted patch worker and candidate workspace

The patch worker is a bounded bridge from an accepted remote baseline to a
retained patch proposal. It is not a test executor, verifier, approval service,
or publisher.

`BaselineJournal.accept(..., queue_patch=True)` moves a matched signed baseline
to `PATCHING` and queues its lease in the same transaction. A patch worker claims
only that stage, reloads the exact accepted baseline action, optionally asks the
existing collection broker to recheck current membership and consent, and makes
one budgeted model request through the existing patch controller. A successful
proposal is retained by `PatchJournal` and atomically queues `VERIFYING`. An
uncertain model submission remains sticky; a restart cannot submit it again.

The operator launcher is default-off. The only executable patch mode is:

```powershell
uv run python -m scripts.launch_authenticated_worker --run --env-file .env.worker `
  --patch --allow-live-inference --estimated-call-microusd <declared-estimate> `
  --max-ticks 1
```

It rejects combined triage/patch modes, missing opt-in, and estimates outside the
configured range. Its child receives the database URL, collection broker key,
broker URL/path policy and, only in inference mode, the model configuration. It
does not forward GitHub, vault, approval, or inherited provider credentials.
Running the command is a user-authorized model-cost action; this implementation
does not run it during ordinary tests.

`prepare_candidate` rebuilds the original, manifest-verified source plus the
immutable reproduction test, checks the retained patch/run/grant/context
bindings, and applies exact unified-diff hunks in memory. It rejects fuzz,
offset/context mismatch, new/deleted files, test changes, stale bytes and scope
changes. The returned private tar archive has a new content hash and explicitly
cannot authorize execution. Candidate and clean execution still require distinct
durable phase-specific upload/submission records, measured workspaces, remote
receipts and the seven-gate verifier.

## Local acceptance

On 2026-10-26, focused disposable-PostgreSQL acceptance ran six patch-worker
cases with exit 0 (`docs/evidence/patch-worker-focused.xml`), including queued
baseline-to-patch-to-verification handoff, fresh broker authorization denial,
and sticky uncertain inference. Unit/contract acceptance ran 1,037 cases with
exit 0 (`docs/evidence/patch-worker-unit-contract.xml`); candidate and launcher
focus ran 39 cases with exit 0 (`docs/evidence/patch-launcher-candidate-unit.xml`).
Ruff check/format passed over 278 files and mypy passed over the ten changed core
modules. These use fake model/provider transport and are not a live inference,
sandbox execution, signed gate result, VERIFIED repair, approval, or PR.
