# Durable baseline worker

Implementation e78ea60 connects authenticated reproduction retention to a baseline
queue and admitted one-shot dispatch. Follow-up a061648 requires saved/measured
JUnit output and exercises the actual REST serializer with mocked HTTP.

## Workflow

Authenticated triage commits the reproduction artifact, REPRODUCING checkpoint
and queued baseline job atomically. The bounded retry count resets only at this
new stage handoff; replaying retention cannot reset it again. Legacy triage mode
keeps its previous finish-job behavior. Collection workers now explicitly claim
only collection stages, so they cannot consume queued baseline jobs.

`tick_prepared_baseline` claims only REPRODUCING. It reloads the integrity-checked
reproduction and resolves an existing immutable preparation from an operator-owned
`PreparedBaselineProvider`. Resolution must be read-only and stable across restart.
It is not a clone/upload/materialization tool or permission to repeat preparation.
The context manager releases client handles, not remote compute or saved images.

The provider supplies BaselineAction, capability attestation/public-key verifier
and a PreparedNebiusBackend-compatible port. These are trusted deployment inputs,
never browser/model JSON. The action is recorded through BaselineJournal before
`dispatch_baseline` checks current scope, budget, lease/version and assessment.

### Concrete provider (80d544f)

`services.runner.baseline_provider.NebiusBaselineProvider` implements the port
with the actual PreparedNebiusBackend. Supply a stable BaselineAction,
PreparedImageBinding, capability attestation, trusted public-key verifier and
SandboxConfig from privileged deployment configuration. No browser/model
payload supplies these inputs. Construction validates exact plan/execution/tree,
image, runner and configuration bindings without network I/O.

Its run_id is derived from the immutable action. The baseline worker filters the
database claim by both REPRODUCING and that run ID, leaving other queued incidents
untouched. Resolution rechecks the retained reproduction/source/context and
cancellation before opening a client. Context exit closes that client on success
or error. Stable action identities survive process restart; no timestamp or
execution ID is regenerated during resolution.

Live construction and worker dispatch each require explicit allow_live=True.
Without a test transport, construction fails closed until authorized. Any injected
transport remains TEST_TRANSPORT and cannot supply production evidence. Always
use this provider through tick_prepared_baseline; direct backend submission is a
low-level operator API without the durable journal's duplicate-submission guard.
This provider does not create or attest a workspace, register production keys,
or claim remote compute has terminated when a client closes.

The dispatcher requires SAVED_JUNIT or MEASURED command mode. Plain disposable
pytest plans cannot lose their only report and still count as a valid baseline
submission. It records an immutable assessment/runner/configuration/plan binding
before network I/O. OperationJournal then commits SUBMISSION_UNKNOWN before POST;
the returned provider ID is appended as OPERATION_KNOWN. No result is called a
passing/failing test here. Signed report collection and baseline acceptance remain
the separate existing `receive_baseline` / `accept_retained_baseline` boundary.

## Recovery and denial

- Concurrent dispatch claims permit one provider submission.
- Restart discovers a known operation or returns SUBMISSION_UNKNOWN; it never
  blindly resubmits, refunds budget or renews retained timestamps.
- A crash after budget reservation can resume its exact unused reservation.
- Legacy operation/receipt records prevent new submission. A competing operation
  record between claim and POST also prevents submission.
- Cancelled, stale, expired, wrong-scope, unassessed and mismatched runner/config
  inputs fail closed. Real provider dispatch requires separate explicit live opt-in.
- Existing GET-only operation recovery can inspect known provider IDs. It cannot
  reconstruct missing IDs or turn unsigned observations into signed receipts.

## Database and startup

Migration 0028 adds immutable `baseline_submissions` and a SUBMISSION_CLAIMED
baseline audit kind. Grant the baseline worker SELECT/INSERT on that table,
alongside existing baseline action/reservation/operation and run/job/event grants.
No UPDATE/DELETE, auth credential access or repository grant writes are required.
Only disposable test databases were migrated during development; the user's
configured application database was not automatically upgraded or regranted.

Downgrade refuses to discard a nonempty submission ledger. Stop scheduling and
perform an explicit archival/rollback review first; deleting claims could permit
duplicate remote work. Historical baseline audit rows are preserved.

The worker is an internal typed controller. No default production preparation
registry, public execution route or automatic sandbox dispatcher is installed.
Deploy it only with actual independently established prepared workspace, image,
configuration, assessor/collector identity and restricted role bindings.

## Source checks: 2026-10-25

Reviewed official Nebius references:

- [Execution lifecycle](https://docs.tokenfactory.nebius.com/sandboxes/concepts/execution-lifecycle):
  operations use fresh compute; saved images carry files rather than live process
  state; client waits can end separately; operation success and process exit differ.
- [Overview](https://docs.tokenfactory.nebius.com/sandboxes/overview): reuse the
  returned saved image identity and inspect both operation and process results.
- [Security and data handling](https://docs.tokenfactory.nebius.com/sandboxes/operate/security-and-data-handling):
  guest networking defaults enabled; each relevant REST spawn must explicitly
  disable it, and API credentials must stay outside guest code.

Inspection of installed contree-sdk 0.3.6 ImageLike.run confirms that it exposes
no networking argument. The admitted path therefore uses the existing REST
serializer, not an invented SDK parameter. The real-serializer contract test
checks network disabled, saved report filesystem, no shell and no preserved env.
The older SDK adapter is not promoted to production execution by these changes.

The choose-an-interface page was unavailable through the documentation tool;
no claim depends on that inaccessible page. No provider request or charge was
needed for these contract tests. Small inert fixtures and synthetic signers
validate application behavior; they do not prove production assessment facts.

## Remaining live acceptance

Materialization deployment and trusted measured collector bindings are still
required before real baseline execution can be accepted. Then connect retained
baseline to patch generation, admitted candidate/clean replay and all mandatory
verification gates. Exact human approval, isolated GitHub write brokerage and
live PR/hidden evaluation remain M4 requirements. These exits are unchanged.

## Source packaging for materialization

`services.runner.source_archive.build_source_archive(manifest, files)` packages
already admitted original source bytes into a deterministic uncompressed USTAR
archive. It performs no filesystem reads, extraction, execution or network I/O.
Callers must supply the exact manifest path set, original byte lengths, SHA256
and Git blob IDs. Sanitized model context is not a substitute for original bytes.
The archive uses explicit parent directories, uid/gid1000, timestamp0, file0644
and directory0755 modes. It rejects unrepresentable USTAR paths and validates the
result through the same archive_identity primitive used by workspace measurement.
Both the complete wire archive (including tar overhead) and expanded contents
must fit65536bytes; at most128 members including directories are admitted.

This primitive is not yet wired to provider upload or a durable materialization
journal. A manifest subset is not a complete repository checkout; executable mode
metadata is not present in SourceManifest and is deliberately not reconstructed.
Current authorization, full-tree completeness, remote extraction/upload, immutable
saved-image measurement and independent capability/collector bindings remain the
trusted controller's responsibilities. The returned bytes are private source data;
do not put them into public events, logs or model memory. No execution authority
or VERIFIED status follows from successful packaging.
