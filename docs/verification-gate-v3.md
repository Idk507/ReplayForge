# Verification gate payload v4 and phase-specific bindings

`replayforge.verification-gates.v4` adds immutable image UUID binding to the
authenticated gate envelope. `GateContext` carries exactly four
`GatePhaseBinding` records: `BASELINE`, `CANDIDATE`, `EXISTING_SUITE`, and
`CLEAN_REPLAY`. Each stores the image UUID, image digest, runtime configuration
reference, and phase workspace digest. The three non-execution gates bind to
the candidate runtime identity. Baseline image/configuration also match the
legacy top-level fields retained for run-level compatibility.

Every signed gate record must match the corresponding phase binding, source,
patch, and retained artifact. `GateVerifier` retains the seven mandatory gate
names and PASS/FAIL/MISSING semantics. The envelope purpose and schema version
changed from v3 to v4 because prior records do not bind image UUIDs.

`ProductionGateSigner` accepts an operator-provisioned signer callback and an
explicit key ID. It generates no keys, fails closed when the callback is
unavailable, and refuses `TEST_TRANSPORT` payloads. The matching public key
must be independently injected into `GateVerifier`. `VerificationJournal`
persists the independently evaluated signed envelope idempotently;
`get_verified` revalidates the persisted signature and retained evidence bytes.

## Current focused acceptance

- `uv run pytest tests/unit/test_gate_signing.py tests/unit/test_authenticated_gates.py tests/unit/test_verifier_gates.py -q` — 45 passed.
- `uv run python -m scripts.test_postgres --pytest-target tests/integration/test_verification_journal.py --evidence-name m3-gate-persistence-revalidation.xml --timeout-seconds 300` — 33 passed, 0 failed, 0 skipped on disposable PostgreSQL 18.6. Includes signer-to-journal acceptance and persisted tamper rejection. JUnit: `docs/evidence/m3-gate-persistence-revalidation.xml`.
- Targeted Ruff, format check, mypy, web lint, and web typecheck passed. The focused browser check and build are recorded in `docs/evidence/m3-checkout-end-to-end-acceptance.md`.

All signatures and reports in these tests are synthetic. This remains a verifier
and signer boundary, not a complete production gate producer. A trusted worker
must still load all four phase plans, operation records, receipts, runtime
preparations, source/patch lineage, and retained assessor payloads from durable
journals; derive the seven gates; and invoke only externally configured trust.
No live execution or VERIFIED M3 result exists.
