# Candidate and clean-replay qualification

`ReceiptVerifier.inspect_replay` authenticates an Ed25519
`SignedReplayReceipt` before inspecting its bounded JUnit bytes. Its signature
purpose is `replayforge.replay-receipt.v1`; baseline envelopes keep their
existing purpose and canonical bytes. Keys are explicitly configured public
keys, with no environment discovery or automatic signing authority.

The caller supplies the immutable dispatched plan, operation ID, expected
patched tree digest and reproduction function name from trusted retained
actions. The plan hash binds run/repository/source, image, patch, reproduction
artifact, paths and exact command configuration. The receipt must be terminal,
fresh, from the configured origin, and show no timeout, signal or tree change.
The actual report digest must match the signed observation.

An exit-zero process alone is insufficient: the report must contain the exact
regression function in its expected module, at least one case, consistent
counts, unique identities, and no failure, error or skipped case. Invalid XML,
DTD/entities, oversized reports, empty suites and missing targets cannot pass.
Safe structured results contain counts and reason codes, not raw log messages.
Authentic failing process/test observations return REJECTED_SIGNED_REPORT;
signature, context and transport-observation denials raise ReceiptDenied.

`services.verifier.replay.inspect_replay_pair` validates candidate and clean
replay together. It requires identical requests except phase, the same expected
patched tree, and three distinct baseline/candidate/replay operation identities.
It authenticates each report against its own planned action. A report from one
phase cannot be substituted for the other. Expectations must be loaded from
durable dispatch, never copied from incoming reports.

Both matching results still have `can_authorize_verification=false`. Distinct
operation identities establish correlation, not independent fresh compute or
runner isolation. The trusted collector must observe those facts. Existing
suite, patch-scope, security and artifact gates remain separately mandatory.
There is no signing key, remote submission, public endpoint, checkpoint write,
automatic inference or execution in these modules. Durable candidate delivery
and worker scheduling remain integration work; these pure functions must run
inside that trusted boundary before gate production.

Tests use small synthetic JUnit documents and ephemeral test keys. They exercise
the protocol and negative cases, not a live collector or production repair.

## Observed validation, 2026-10-21

Base commit: `27c854e`, plus this increment. No cloud calls or remote writes
were performed. Local PostgreSQL testing used a random disposable database and
restricted test roles, cleaned by the existing harness.

- `uv run pytest tests/unit/test_replay_pair.py tests/unit/test_replay_receipts.py
  tests/unit/test_runner_receipts.py tests/unit/test_baseline_inspection.py -q
  --junitxml=docs/evidence/m3-replay-receipts-focused.xml`: exit 0, 81 passed,
  no failures/errors/skips.
- `scripts.test_postgres.main` with evidence name
  `m3-replay-receipts-postgres.xml` and a 300-second child ceiling: exit 0,
  940 passed, zero skips, 198.07 seconds, one upstream Starlette/AnyIO
  deprecation warning. The password was supplied in memory, not persisted.
- `uv run ruff check`: exit 0. `uv run mypy`: exit 0, 87 typed source files.
  Ruff formatting for the five changed Python files and scoped Git whitespace
  checks passed. Global format/diff checks identified a concurrent unrelated
  trailing space in `services/runner/smoke.py:97`; that edit was preserved.
- Scoped credential-pattern scan passed. Dependencies and UI were unchanged;
  their network audits/browser checks were not repeated for this backend slice.
- `uv run python -m scripts.check_checkout`: exit 0, M2_FIXTURE_ACCEPTED,
  verification/execution authority both false.

The first test iteration exposed a fixture mistake: the proposed changed test
digest equaled the original. It was corrected to a different digest and the
final full suite above passed. No tests or assertions were removed to pass.

M3 remains open. Next integration work is durable candidate/replay delivery
through the worker, followed by actual collector-backed gate production. Live
acceptance requires real independent execution and trusted image/configuration
observations; matching synthetic reports do not supply those facts.
