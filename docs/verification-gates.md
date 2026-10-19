# Mandatory gate evaluation

The unsigned `services.verifier.gates.evaluate` function is a consistency
preview. Its positive status is now GATES_SATISFIED, never VERIFIED, and its
can_authorize_verification field is always false. Repository search found no
production consumers of the previous positive status; tests and future callers
must use the authenticated boundary for a verification decision. A caller's
REMOTE_SERVICE label is not authentication.

Seven policy-owned gates are mandatory: BASELINE_REPRODUCED,
PATCHED_REGRESSION_PASS, EXISTING_SUITE_PASS, CLEAN_REPLAY_PASS, PATCH_SCOPE_PASS,
ARTIFACT_INTEGRITY_PASS and SECURITY_GATE_PASS. Missing or explicitly MISSING
gates cannot pass. Duplicate gates and inconsistent source/patch/workspace
identities are inconclusive. Any reported failure prevents success. Optional
STATIC_ANALYSIS_PASS cannot hide a reported failure or unknown result.
Models cannot change the required gate set. Input models are revalidated so
model_copy/model_construct cannot bypass status and origin constraints.

## Authenticated boundary

`services.verifier.authenticated.GateVerifier` takes an explicit copied registry
of 1–16 Ed25519 public keys belonging to a trusted deterministic gate producer.
There is no discovery, signing, environment access or automatic key provisioning.
The signed envelope binds a dedicated purpose, algorithm, key ID, fixed policy
version, run/repository IDs, run version, source commit, patch/test/image/runtime
configuration identities, manifest identity and observation time.

Expected GateContext must come from the owner-scoped durable checkpoint and
trusted runner configuration. Never construct expected values from the received
envelope or a model proposal. The workspace SHA256 identifies the common replay
input bundle, not a claim that baseline and patched Git trees are identical.
The producer must independently validate actual before/after tree identities.

Verification authenticates the envelope and checks a trusted aware clock:
maximum age 300 seconds and future skew five seconds. TEST_TRANSPORT is denied.
It rebuilds the manifest against actual bounded artifact bytes, requires both
patch and reproduction artifacts, checks every gate's artifact/source/patch/input
binding, and requires separate operation IDs and artifact references for
baseline, patched regression and clean replay. IDs are correlation checks;
independence of actual compute must be observed by the trusted producer.

Only complete authenticated, correlated evidence can produce the local VERIFIED
decision. Failed gates produce NOT_VERIFIED; incomplete or inconsistent evidence
cannot pass. Safe denials use bounded GateDenied codes. Verdict metadata includes
run context, observation time, signed-envelope hash and manifest reference;
raw report bytes, signatures and credentials are not output as telemetry.

## Trust and deployment limits

The producer must be deterministic application code outside model control. It
must validate signed runner reports, intended baseline failure, existing suite,
patch scope, security findings, replay and actual artifacts before signing gate
records. A signature authenticates that configured producer; this consumer does
not re-run tests or infer gate truth from arbitrary artifact text. It does not
replace runner capability admission or establish OCI/configuration facts itself.
Do not give this signing key to models or untrusted sandbox workloads.

The returned object is an internal calculation, not an approval token. Its
can_authorize_publication is always false. No API route, checkpoint mutation,
PR publication, remote request or execution is enabled. Future durable ingestion
must call this verifier under current owner/lease/version/consent checks, not
accept a serialized verdict from a caller. Signing deployment, artifact storage
ACL/retention, durable verdict ingestion and live end-to-end M3 acceptance remain
unfinished. The unsigned M2 fixture observations cannot be promoted to VERIFIED.

Tests use small synthetic artifact bytes and ephemeral signing authority,
including REMOTE_SERVICE-shaped fixtures to exercise the successful contract.
They do not demonstrate a live producer, runner assessment or verified repair.
