# Checkout fixture acceptance — 19 October 2026

The fixed-fixture contingency in todo.md now has a real inferred regression,
observed behavioral baseline failure, real inferred application patch, Git patch
application, passing candidate and passing clean replay in separate disposable
Nebius operations. This is `FIXTURE_REPAIR_PASSED`, not `VERIFIED`.

Command: `uv run python -m scripts.e2e_checkout --live --env-file .env --resume-baseline`
returned exit 0. It reused the previously captured baseline and generated test;
this corrective attempt made one inference request and two sandbox submissions,
with no retries. Only the small checkout fixture was sent. No Tavily, GitHub
write, local generated-code execution or entire-repository upload occurred.

Evidence: `evidence/m2-checkout-e2e/report.json`, `triage.json`, `patch.json`
(the latter two in the same directory). Earlier inconclusive reports are
preserved alongside them. The baseline operation is
`01a14f6f-243c-7507-8825-f190960d5636`; candidate and replay are
`01a15279-3b82-743c-bbeb-44ffa25c3a93` and
`01a15279-4376-729d-925b-e6494dd4e3b8`. All confirmed terminal compute.

Baseline exit 1 reports the intended assertion, no setup error and one test.
Candidate/replay exit 0 report one test each. All source commits are
`64acd34050cdb95bf69e27d1564ca21434145198`; the unchanged generated test SHA256 is
`7527a1c244c21132f9fe0f2e480e4edfd78fd11406314b77513f4bae99f3e46d`.
Candidate/replay Git trees both equal `820fa4233884b9e9967a4cf0661df38403668b3c`.
Admitted patch SHA256 is
`deb161a47fe446ea621bd49705fc24978106dc30beecdd366c325501e0b8433a`.

Local reproduction demonstrated that a valid replacement missing a final
newline was incorrectly rejected. The gateway now appends that newline before
deterministic diff construction. Schema, size, empty-patch and diff-policy
errors have distinct sanitized codes; path restrictions remain enforced.
Previous live response bodies were not retained, so the exact cause of every
earlier generic rejection cannot be established retrospectively.

Command/path rejection tests capture audit events in
`evidence/m2-checkout-policy-audit.log`, without submitting remote operations.
Completed or partially executed candidate attempts cannot use baseline-resume
to duplicate cloud work. Evidence writers explicitly use LF on Windows.

The harness is standalone; it does not pretend to checkpoint the durable worker
graph or possess a production signer. Provider UUID is not a verified OCI
digest. Guest network/identity observations and process rlimits do not attest
provider hard caps, retention or deletion. The broader repository runner stays
behind its existing admission gate. Mandatory verifier gates, existing-suite
and hidden-oracle acceptance, production graph integration, and human-approved
PR publication remain separate outstanding work. No merge or deployment occurred.

Build base: `9696f99557615d531970c3ab0b7c0982011311d1` plus local changes.

Final complete local suite with disposable PostgreSQL: 718 passed, no skips,
one upstream AnyIO deprecation warning, exit 0, 90.66 seconds. Evidence:
`evidence/m2-checkout-postgres-tests.xml`. Ruff lint/format, strict mypy,
patch checksum/operation identity check and targeted credential-value scan
also passed. Dependency vulnerability scanning and UI checks were not rerun;
this change neither adds dependencies nor modifies the UI.

## Repeatable M2 acceptance check

`uv run python -m scripts.check_checkout` validates the retained artifacts
offline and returns `M2_FIXTURE_ACCEPTED`, exit 0. It checks patch and test
digests, allowed patch paths, original source/tree identities, three distinct
operation IDs, matching images, terminal outcomes, actual test presence,
behavioral baseline failure and matching successful candidate/replay trees.
Changing a digest, source, image, tree, operation ID or outcome rejects the
evidence. This consistency result cannot authorize execution or VERIFIED.

The fixture CLI now uses the same checker before publishing success. A malformed
generated test has an explicit INCONCLUSIVE result and never reaches a sandbox.
The production baseline reducer also now supports transactional acceptance of
a retained, reserved and freshly reauthenticated remote baseline; synthetic
transport, stale version, wrong owner/report, cancellation and missing command
reservation cannot open PATCHING. Invalid signed reproduction closes the lease
as INCONCLUSIVE; failed audit writes roll back the transition.

The milestone's fixed-fixture exit criteria can therefore be inspected without
spending again. Production integration remains separate: no available trusted
signer/capability evidence has been supplied, the polling worker still dispatches
collection only, and generic repository admission stays disabled. The signed
reducer tests are local contracts, not live production attestation. Full M2
production completion is not claimed by this scoped exit result.

Closure validation: 746 tests passed, zero skipped, one upstream warning,
92.23s (`evidence/m2-closure-exit-handling-postgres.xml`). Ruff lint/format and
strict mypy passed. Authenticated exit 0/2 observations are retained as rejected
baseline inspections and cannot enter PATCHING; assertion verification still
requires exit 1. Implementation/evidence commits are 15d131e and 7cf4349 on
codex/m2-checkout-closure. Committed artifact bytes match the checksum ledger
`evidence/m2-closure-acceptance.json`. No cloud call was needed for this closure.
