# Signed runner capability gate

CapabilityVerifier is a pure, offline gate for the M2 runner capability portion
of dispatch. It does not execute code, register a runner, contact a provider,
reserve budget, create a dispatch permit or change a run stage. No new execution
method is added to the M0 SandboxBackend; its fixed synthetic probe stays intact.

The operator must explicitly configure one runner UUID, backend, expected origin
and 1–16 trusted Ed25519 public keys belonging to an independent capability
assessor outside the untrusted workload. There is no default key, key discovery,
signing service or production assessment in this repository. Profiles bind the
runner, backend, verified OCI image SHA256, runtime configuration SHA256, policy
version, origin, issue/expiry
times, enforced resource limits and bounded evidence references. The canonical
signature binds purpose, algorithm, key ID and payload; baseline receipt
signatures cannot be replayed as capability signatures. The verifier's expected
configuration digest must come from a trusted immutable runtime descriptor,
covering network, filesystem, identity, resource and artifact settings. A future
dispatcher must verify that exact configuration is applied to the actual runner;
this module does not calculate a digest from provider state. Configuration drift
requires a new assessment even when the image and runner UUID are unchanged.

Every one of the 22 required controls must have an evidenced PASS:

- Remote Linux and verified image digest; non-root, read-only root, no privileged
  mode, no host mounts and a syscall filter.
- Offline execution, denied metadata/control-plane access and no inherited
  credentials.
- Enforced CPU, memory, process, wall-time, writable-byte, output-byte and file
  count caps.
- Ephemeral workspace, owned-operation termination, artifact integrity and an
  assessed retention policy.

Version-one application policy caps are 2 CPU cores (2000 millicores), 512 MiB
memory, 64 processes, at most 60 seconds or the command's shorter timeout, 64 MiB
writable data, 128 KiB captured output and 1024 files. These are local admission
policy choices, not established Nebius quotas or guarantees. Claimed smaller
limits are safe but may cause an explicit setup/test failure. An approved
retention policy does not mean immediate deletion of provider logs or images.

Unknown, failed or omitted controls, missing limits or excessive enforced caps
produce DENIED. Signature/schema/key, runner/backend/origin/image binding and
freshness failures raise stable AdmissionDenied codes. A profile's lifetime is
positive and at most 24 hours; expiry is exclusive, and at most five seconds of
future clock skew is allowed. The clock must come from trusted application code.
TEST_TRANSPORT always produces DENIED, regardless of its claimed controls.

CAPABILITY_REQUIREMENTS_MET accepts an authenticated assessor's claims for this
one gate only. A signature authenticates the assessor; this module does not
resolve referenced artifacts or independently repeat their experiments. The
assessor must validate real evidence before signing. Production key provisioning,
artifact resolution/retention and ongoing re-assessment remain pending. All
decisions have can_authorize_execution=false, a plan/attestation hash, trace,
timestamp, bounded missing-control names and non-retryable policy outcome.
No raw logs or open-ended error text enters these decisions.

A future trusted submitting controller must additionally check scope, action and
artifact identities, cancellation, atomic budget reservation, lease/version and
one-use submission/reconciliation state immediately before dispatch. It must
re-evaluate the original attestation instead of trusting a caller-created decision
or treating the recovery journal as an execution permit. No network dispatcher is
wired by this change.

Current boundary: the previously observed live M0 probe establishes only its
synthetic observations. It is not a capability attestation and cannot qualify
repository execution. Image digest, hard resource/file caps, read-only root,
independent termination and retention evidence remain incomplete. Acquire real
assessor/provider evidence or configure a designated remote Linux runner before
enabling repository execution. Safe fallback remains the fixed M0 synthetic
probe and offline harness tests; Windows/API-host execution stays forbidden.

Local tests use ephemeral synthetic signers, including explicitly invented
REMOTE_SERVICE contract claims to test the successful branch. They are not live
runner evidence. M2 stays partial until a genuine baseline and clean patched replay
pass in an admitted remote runner.
