# Provider-operation recovery metadata

Migration 0009 and OperationJournal add bounded, append-only lifecycle metadata
for a previously reserved baseline action. This is an internal control-plane
adapter, with no model tool, HTTP route, provider submission or polling loop.

Each execution has at most three records, in this order:

1. SUBMISSION_UNKNOWN: an uncertain submission boundary, with no operation ID.
2. OPERATION_KNOWN: the adapter reports one UUID for the provider operation.
3. COMPUTE_TERMINAL: the adapter reports that this same operation is terminal.

All records explicitly carry UNVERIFIED_ADAPTER_INPUT and
can_authorize_execution=false. A terminal assertion does not prove a baseline
failure, test success, sandbox isolation, deletion, retention or authenticity.
Signed runner receipts and actual reports remain separate evidence requirements.
No secrets, URLs, raw reports, free-form errors or provider logs are accepted.
The provider field is a fixed identifier, not an assertion of live transport.

Writes lock the owner-scoped run and live worker/token lease, require the current
checkpoint version, verify the canonical action hash and its existing reservation,
and insert a content-addressed record. Starting a new unknown-submission record
also rechecks REPRODUCING, cancellation, current repository consent and immutable
source/test bindings; completed actions cannot start one. The already-reserved
last command remains recordable at zero remaining command budget.

Binding and terminal assertions may arrive after cancellation or consent
revocation, under the same live lease/version checks. Retaining recovery metadata
cannot schedule more work. It neither consumes/refunds budget nor changes a run's
version, stage or baseline result reference. Post-terminal reconciliation without
a live lease remains an operator/recovery-controller design task.

Exact replay returns the historical record, including its original DB timestamp,
worker and version. It never means “submit again.” Conflicting IDs/providers and
out-of-order phases fail closed. A provider/operation UUID can bind to only one
execution across runs. The journal does not implement an exactly-once external
effect or issue a one-use dispatch permit. Any future submitting controller must
distinguish its first durable submission claim from recovery and reconcile an
unknown outcome through the provider before considering another operation.

Reads require the owner and an unexpired parent action. Canonical hashes, indexed
columns, sequence, provider and operation bindings are checked before returning
history. Parent action expiry limits reads to seven days; rows/backups are not
physically purged. SELECT/INSERT is sufficient on baseline_operation_records;
runtime UPDATE/DELETE is denied. Privileged database tampering remains outside
checksum protection. Records carry the run trace, worker, checkpoint version and
DB timestamp for audit; they are not yet published through public SSE.

Local tests use TEST_TRANSPORT and synthetic checkpoint setup, exercising restart,
concurrent replay, cancellation/lease/version/owner denial, final-budget handling,
ID reuse, insertion rollback, permissions, expiry and corruption. Live provider
lookup/polling, signed operation correlation, cleanup controller, runner admission
and genuine baseline/patch execution remain unverified. M2 remains partial.
