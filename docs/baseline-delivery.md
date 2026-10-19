# Durable signed baseline delivery

Migration 0015 adds nullable, bounded `report_payload` to `baseline_receipts`.
New receipts retain the exact inspected JUnit bytes in the same transaction as
the signed envelope, inspection and receipt audit row. The existing restricted
runtime role retains SELECT/INSERT privileges only. No public report endpoint,
new model tool, signing key or remote execution authority is introduced.

`BaselineJournal.report(execution_id, owner)` checks owner scope, action/receipt
expiry, canonical receipt integrity, byte bounds and the signed JUnit SHA256.
It returns private report bytes to trusted application code. Damaged or missing
bytes fail closed. Historical receipts have NULL report_payload and return
BASELINE_REPORT_UNAVAILABLE; they are not silently backfilled or resigned.
Downgrading discards report bytes and requires an archival/rollback review.

The producer must redact credential-bearing output **before signing**. This
receiver does not modify signed bytes: doing so would invalidate their content
identity. A trusted collector outside the guest must independently establish
process/source/image facts. The producer, its deployed key, isolated runtime
and assessment are not supplied by this delivery module. Reports are private
artifact data; do not include them in events, checkpoints or telemetry.
Read expiry is not physical deletion or an encryption-at-rest guarantee.

`services.orchestrator.baseline_delivery.receive_baseline` is an explicit
internal delivery entry point. It accepts a bounded signed report, uses the
existing journal to authenticate and retain it, then calls the existing reducer
with the stored bytes. It does not run a test, submit work or call a model.

If the process dies after retention but before checkpoint acceptance,
`accept_retained_baseline` recovers without another producer request. Fresh
acceptance still needs the active owner/lease, current consent, reserved action,
valid configured signing key, fresh signature and matching baseline behavior.
The reducer alone enters PATCHING or closes invalid reproduction INCONCLUSIVE.
TEST_TRANSPORT cannot open PATCHING. A service-shaped ephemeral test signature
is not a production signing deployment or live execution evidence.

Concurrent delivery and restart after acceptance reconcile the committed
checkpoint and retained content rather than repeating the transition. Accepted
PATCHING/VERIFYING reconciliation requires the checkpoint's exact retained
receipt hash; invalid-reproduction reconciliation only reads the already closed
INCONCLUSIVE status. Reconciliation does not refresh signature timestamps,
authenticate a new action, grant patch permission or make a provider request.
Conflicting redelivery is rejected even after checkpoint completion. Cancellation,
unrelated stages, expired artifacts and mismatched owners fail closed.

Retention and acceptance intentionally use separate transactions. Acceptance
audit failure rolls back the checkpoint while preserving the authenticated
retained report, making a later fenced acceptance possible. Receipt audit failure
still rolls back both receipt and report together. The next independently
authorized controller can call the existing patch path only after the trusted
baseline reducer has accepted the evidence; this entry point never calls it.

Tests use small synthetic XML and ephemeral synthetic signers with the real
disposable PostgreSQL harness. No cloud call is needed to validate delivery.
