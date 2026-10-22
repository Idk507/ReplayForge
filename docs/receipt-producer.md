# Measured prepared execution and receipt production

`services.runner.producer.MeasuredPreparedBackend` is an opt-in trusted-worker
implementation of the existing prepared execution port. It extends the existing
REST backend; it does not install a default signing identity, broaden repository
admission, discover credentials or execute workloads on the API/developer host.

## Execution and evidence contract

1. Construct with a trusted `PreparedImageBinding`, capability attestation and
   public-key verifier, plus an explicitly supplied Ed25519 receipt key/key ID.
   The key is worker-only. Construction performs no HTTP. Custom transports are
   always TEST_TRANSPORT; production HTTP requires `allow_live=True`.
2. Plan with `plan_pytest(..., measured_workspace=True)`. This creates the new
   closed `PYTEST_FILES_MEASURED_V1` template, saves the result image and adds
   Python `-B`. Existing V1 and saved-JUnit templates/identities are unchanged.
   `-B` prevents pytest/import bytecode writes from changing the measured tree;
   `-I` would ignore a PYTHONDONTWRITEBYTECODE environment setting. Cacheprovider
   remains disabled. Guest writes to other workspace files remain detectable.
3. Use `dispatch_replay`, not a model/browser call, for leased replay submission.
   The durable submission intent commits before backend I/O. The backend checks
   exact plan/execution/tree/image/runner/configuration bindings and capability
   admission before its first GET. It independently reads and hashes the prepared
   `/workspace/repo` archive, then compares against the trusted expected tree.
   A mismatch prevents POST. Admission is checked again before the single POST.
4. `collect_receipt(owned_operation_id)` checks owned operation/source-image
   correlation and requires SUCCESS or FAILED with a distinct saved result image
   and strict bounded process state. It reads actual private JUnit bytes and
   independently measures the result-image workspace. It signs actual source and
   final tree refs, report hash, execution/run/plan identities, process facts,
   origin and observation time using the existing canonical signature format.
5. Baseline and replay use separate existing signature purposes. Nonzero exits,
   timeout/signal facts and changed trees are preserved honestly. A signature
   never substitutes for the deterministic report checks: mutation/timeout are
   rejected by ReceiptVerifier; pytest exit failures cannot become passing replay.
   Malformed, empty, pending, missing, oversized or uncorrelated evidence is not
   signed. Provider stdout metadata is discarded; raw JUnit stays private and
   untrusted, and may itself contain workload output. No hidden reasoning or
   signing secret is retained.
6. Repeated successful collection returns the same in-memory result/signature
   without HTTP or refreshing observation time. HTTP close remains distinct from
   compute cancellation; inherited owned cancellation/status methods remain
   explicit. Saved-image/log physical deletion is not proved.

The ProducedReceipt carrier is private. Its signed envelope is the authenticated
part; workspace summaries/operation ID are contextual metadata and must not be
accepted as independent authority. No public signing route is exposed.

## Remaining boundaries

The archive limit remains 64 KiB and 128 entries, suitable for tiny fixtures.
This is not general repository materialization. The materializer/operator must
still independently establish pinned source, exact test/patch, verified OCI to
provider-UUID mapping and assessed execution/retention configuration. A made-up
binding or self-signed capability profile is not production evidence.

Pre-execution measurements and produced receipts are currently held in the
backend instance. They are not recoverable through a fresh backend after worker
death. The existing durable dispatcher prevents duplicate submission, including
unknown POST outcomes; it does not restore this producer's state. A restarted
worker must not reconstruct a receipt from expected hashes or renew an old
observation timestamp. Durable measurement/receipt retention and controller
handoff, all seven gate production and authentic live M3/UI acceptance remain
open. Default harness behavior still stops at REPRODUCING.

No production signing key or fabricated PASS assessment was generated. Live
acceptance needs separately provisioned trusted identities, admitted prepared
images, and explicit permission for necessary provider charges.

## Tests

Tests use tiny inert Python-text archives, synthetic HTTP and ephemeral signing
keys only. They prove signatures interoperate with existing baseline/replay
verifiers, failing facts remain failures, admission precedes measurement, and
real PostgreSQL durable dispatch never resubmits on restart (including POST
connection loss). They do not demonstrate a live repair or VERIFIED gate.

Initial RED: `m3-receipt-producer-red.xml.gz` (module absent).
Focused initial contracts: `m3-receipt-producer-focused.xml`, 69 passed, exit 0.
PostgreSQL composition: `m3-measured-dispatch-focused.xml`, 2 passed, 1143
deselected, 8.23 seconds, exit 0. The final full regression is recorded in the
implementation log after completion.

The transport routes/limits reuse the existing documented REST adapters; the
archive endpoint reference is the [official provider OpenAPI](https://eu-north.nebius.computer/static/api.yaml).
No new provider package or dependency is introduced.


## Final regression — implementation 40e2c40

`docs/evidence/m3-receipt-producer-postgres.xml`: 1145 passed, zero failures,
errors or skips, 419.35 seconds console / 419.312 seconds JUnit, exit 0.
SHA256: 2514150c0ced2ef82fc0271fad0d44997570a7777b586e3d7f64539777175c51.
One existing upstream Starlette/AnyIO deprecation warning remains. Ruff check,
Ruff format --check (204 files), strict mypy (101 source files), scoped credential
value-pattern and git diff checks pass. Offline check_checkout still reports
M2_FIXTURE_ACCEPTED. No dependency/UI changes required repeating their prior
audits/browser runs. The disposable database and test roles were cleaned up.

Reproduce full PostgreSQL regression (prompts for local administrator password):

```powershell
uv run python -c "import sys,subprocess; from scripts import test_postgres as t; call=subprocess.run; subprocess.run=lambda args,**kw:call(args,**{**kw,'timeout':480}); sys.argv=['test_postgres','--evidence-name','m3-receipt-producer-postgres.xml']; sys.exit(t.main())"
```

The 480-second override bounds the whole growing test suite, whose measured run
is now about 419 seconds. It changes no individual test timeout, product budget,
security control or verifier acceptance threshold. No test was skipped/suppressed.
No inference, sandbox, Tavily, remote repository write or deployment occurred.
