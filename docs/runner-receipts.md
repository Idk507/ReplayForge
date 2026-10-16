# Baseline receipt signature and correlation checks

ReceiptVerifier accepts an explicit copied registry of 1–16 Ed25519 public keys
and one expected evidence origin. There is no key discovery, signing, filesystem,
provider call or environment access. Production keys are not provisioned. Tests
generate temporary signing keys and always use TEST_TRANSPORT.
The pinned cryptography library provides signature verification; see its
[official Ed25519 documentation](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

SignedReceipt binds purpose replayforge.baseline-receipt.v1, schema version,
algorithm, key ID and the entire ExecutionReceipt payload using sorted compact
UTF-8 JSON. The signature is excluded from its own signed bytes. A separate hash
of the full signed envelope identifies the receipt artifact. The plan reference
hashes the full validated canonical TestCommandPlan, including source/repository,
test/image identities, command template, trace and observation timestamp. Retrieve
the exact retained plan; reconstructing it with another trace/time changes its hash.

Verification checks the trusted key/signature, run/plan/execution IDs, source tree,
unchanged final tree, JUnit hash and configured origin. Baseline process must be
terminal, not timed out/signalled, with exit code 1. The caller supplies a trusted
aware clock: receipt age is at most 300 seconds, with at most five seconds future
clock skew. No key fallback is attempted. Changing the registry requires a new
verifier; deployment key rotation/revocation is operator work.

Execution ID, expected source tree and failure expectation must come from the
durable trusted action/reproduction record, never from workload output or a model
request. This module does not create or reconcile that record. Rebinding a receipt
to another execution, run, plan, tree or report is rejected. Repeated verification
for the same exact expected action is permitted for recovery; there is no in-memory
one-use cache and no durable replay-consumption claim. Transactional reducer/job
fencing and idempotent acceptance are still required.

inspect_baseline hashes the actual report bytes under the existing 128 KiB cap,
verifies their binding to the signed receipt, then uses the bounded JUnit inspector.
MATCHED_SIGNED_REPORT means an authenticated statement and matching report under
the configured key/origin. It does not prove the statement was produced by a safe
runner, establish hardware attestation, reproduce the incident semantically, or
authorize a patch. Every result still has can_authorize_patching=false. The nested
XML inspection preserves its UNAUTHENTICATED_INPUT label; the receipt wrapper
provides the separately checked provenance.

Trusted collection/signing must run outside untrusted workload code. Never give
a guest the signing key or accept source tree/process claims solely from its
stdout. The producer must independently correlate provider operations, validate
actual source/image/artifacts and process termination, then sign validated facts.
Credential-bearing output must be redacted before retained report bytes are hashed
and signed. This module stores no report and does not implement that producer's
redaction pipeline; the exact inspected bytes must match the signed report hash.
This producer, production public-key registration, artifact retrieval/materialization,
runner capability admission and graph acceptance
remain pending. Signatures authenticate the configured signer, not workload truth.
Append-only action expectations and receipt retention now exist (baseline-journal.md).
Budgeted external dispatch and reducer acceptance remain pending; recording or
retaining a receipt never authorizes execution or stage promotion.
Original/final tree hash inventory semantics must be fixed by that materializer.

No live runner receipt was obtained in this slice. Synthetic signed reports cannot
qualify live milestone gates. Existing raw-JUnit inspection remains independently
available and cannot authorize patching. M0/M1/M2 remain partial.
