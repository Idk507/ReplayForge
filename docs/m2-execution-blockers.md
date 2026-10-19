# M2 execution acceptance blockers

Observed 2026-10-18; local base ffcafe2fae11648f3390a647201da4df9a3cffd2.

Update 2026-10-19: the standalone fixed-fixture contingency now passes real
inferred regression, baseline failure, inferred Git-applied patch and fresh
candidate/clean replay. See `m2-checkout-acceptance.md`. The production and
durable-worker limitations below still apply; credentials are working.

Broader M2 integration is incomplete. Successful model JSON generation, fixture-only historical
sandbox results, and local contract tests are separate observations. They do
not establish an integrated admitted repair run.

Credential update: with explicit user authorization, the local `.env` was read
in memory and a read-only whoami call returned HTTP 200. Current sandbox access
is working; missing process environment is no longer an external access blocker.
Observed effective limits include 50 concurrent instances, 3600-second maximum
execution timeout and a 12 GiB maximum layer allowance. These maxima are not
ReplayForge's requested limits. This response did not expose CPU, memory, PID,
read-only-root or retention controls. Sanitized evidence:
`docs/evidence/m2-env-effective-limits.json`. No credential value was retained.

## Concrete runner compatibility gap

The installed contree-sdk 0.3.6 ImageLike.run signature offers no networking
argument. The current SDK adapter therefore cannot demonstrate that it applies
the required network-disabled setting on every preparation and test operation.
Nebius documents networking as enabled by default and requires
`networking.enabled=false` on every relevant spawn to remove the guest interface.
The fixed M0 probe uses a separate path; its evidence does not admit repository
execution through the SDK adapter.

References checked:
- https://docs.tokenfactory.nebius.com/sandboxes/operate/security-and-data-handling
- https://docs.tokenfactory.nebius.com/sandboxes/operate/limits-retention-and-usage
- Local installed SDK: contree_sdk/sdk/objects/image_like/_base.py, run signature.

Next execution work must implement a supported interface that actually sets
the required configuration, record immutable image and operation identities,
and obtain independently assessed capability evidence and a trusted signing
identity. Do not generate invented PASS evidence or substitute unit-test keys.
CPU/memory/process/filesystem and retention controls remain unverified under
runner-admission-v1. Provider documentation directs customers to confirm
required retention/deletion guarantees with the Sandboxes team.

## Integration work still required

The explicit triage controller loads owner-scoped persisted context, reserves
one inference attempt, performs a single request and atomically retains the
validated reproduction. It is not yet connected to the polling worker's full
agent graph. Exact body/model/endpoint identity and operator-estimated cost plus
configured-timeout reservations are now atomic in the existing journals; see
`inference-reservations.md`. Actual elapsed-time/usage reconciliation and live
pricing validation remain open. Provider failures leave sticky uncertain intent and
must not be retried blindly.

Beyond triage, M2 requires admitted remote baseline submission, signed report
delivery to the reducer, generated patch retention/application, candidate
execution and independently correlated replay. The current low-level SDK
fixture adapter must not be wired around the capability gate to manufacture
this acceptance. Cloud-call authorization does not supply missing provider
controls or prove runtime evidence.

Update 2026-10-19: transactional reducer acceptance is now implemented as
`BaselineJournal.accept`, with command-reservation, current-grant, signature,
report-byte, owner/lease/version and rollback checks. Its trusted continuation
calls the existing one-shot patch controller only after acceptance; an invalid
reproduction terminates INCONCLUSIVE. These are executable local contract
tests, not a production signing deployment. The polling worker remains
collection-only; no generic execution or paid inference is silently enabled.
The standalone real fixture artifacts pass the offline M2 acceptance checker.

Known-operation reconciliation update (2026-10-19): the explicit internal
`recover_baseline_operation` controller now connects owner-scoped durable
operation history to one bounded REST status read and a fenced terminal append.
Unknown submission remains sticky, transport faults do not trigger resubmission,
and production admission/signing is not bypassed. See `operation-recovery.md`.
This is tested with real PostgreSQL and mocked HTTP; no live operation was
queried during this increment. General submission, signed report collection,
candidate/replay integration and polling-worker scheduling remain open.

Collection-to-triage handoff update (2026-10-19): collection can now atomically
queue a TRIAGING job via the explicit --queue-triage option. A separate trusted
tick_triage entry point claims only those jobs and invokes the existing
request-bound inference controller; successful test retention closes the job
atomically. Restart after an uncertain request cannot repeat inference. See
triage-worker.md. Default startup remains offline with respect to inference.
Baseline/candidate execution and the remaining graph are not implied by this
handoff, and the production assessment/signing boundary above still applies.
