# Durable baseline expectations and signed receipt retention

Migration 0007 adds baseline_actions, baseline_receipts and baseline_action_events.
BaselineJournal is an internal trusted adapter; no public API, model tool, daemon
or dispatcher is added. It does not change stages, set baseline_result_ref,
or execute anything. The graph reducer remains the only future
authority for stage acceptance. Runtime needs SELECT/INSERT only on all three
tables; migration/operator roles remain separate.

BaselineAction retains the exact immutable command plan, execution UUID, expected
source-tree reference and validated failure expectation. Its canonical JSON and
SHA256 are stored together, capped at 16 KiB. At most six plans may be recorded
per run; each run/plan hash and execution ID is unique. This is a planning cap,
not a reproduction execution budget or an exactly-once submission guarantee.
External submission and authenticated provider reconciliation remain unimplemented.
Bounded unverified lifecycle metadata is described in operation-reconciliation.md.
Do not send a workload merely because a plan was recorded.

Writes lock the owner-scoped run and require a live worker/token lease, exact
checkpoint version, REPRODUCING stage, no cancellation and current persisted
consent/command bindings. Action insertion and PLANNED audit commit together.
Same exact action returns its prior reference; conflicting reuse fails closed.
Concurrent retries serialize through the run lock. No budget is decremented and
the checkpoint remains unchanged during planning and retention.

Migration 0008 adds append-only baseline_dispatch_intents. The trusted
reserve_dispatch adapter locks the owner-scoped run and live lease, checks current
consent and immutable action bindings, then atomically inserts one intent, debits
one sandbox_commands unit, increments checkpoint version and emits BUDGET_RESERVED.
A failed audit/insert rolls back all of these writes. Concurrent retries of the
same execution and original version reconcile to the existing reservation without
another debit, including after the final budget unit. Cancellation, expired lease,
changed bindings, conflicting version or action identity fail closed. A completed
action cannot acquire a new reservation. Runtime needs SELECT/INSERT on the intent
table, in addition to existing run/checkpoint permissions.

The returned version is reconciliation metadata, never an execution permit.
There is no automatic refund, submission or resubmission. Unknown provider outcomes
must be reconciled before external retry. OperationJournal now retains unverified
operation identity/terminal assertions; it performs no provider lookup or dispatch.
Intent rows follow the existing journal retention
boundary; physical purge remains pending. No raw source, reports or credentials
are stored in these rows.

Receipt retention retrieves expectations from the durable action, uses PostgreSQL
time for signature freshness, rechecks consent/checkpoint and verifies the actual
bounded JUnit bytes through ReceiptVerifier. Only signed envelope, safe inspection
metadata and acceptance timestamp are stored. Raw JUnit, logs, source, signing
keys and credentials are never retained by this journal. Both matched and rejected
signed reports may be preserved for audit; neither can authorize patching.
The receipt and RECEIPT_RETAINED audit row commit atomically. Exact signed repeats
reconcile to one stored record; changed envelopes conflict. Freshness is checked
on every retention attempt. After that window, recovery reads the stored record
rather than resubmitting a stale receipt.
Retaining evidence for an already-recorded action remains possible after the
last command consumes its budget. Scope, lease, stage, version and artifact
checks still apply. Planning a new command at zero budget remains denied; receipt
retention does not refund or reserve budget.

Reads require owner and unexpired action/receipt, then check stored hash, closed
schema and canonical bytes. Seven-day read expiry does not delete rows or backups.
Reads are historical artifact retrieval, not current-key verification or approval.
Checksums do not defend against privileged database tampering; a future consumer
must revalidate original signatures, reports and trusted action bindings before
any security decision. Key revocation/rotation and purge policy remain pending.

Audit rows contain execution ID, kind, run version, trace, worker ID and DB time;
no source, failure marker or report text. They correlate to run history but are
not yet exposed through the public SSE route. No change to existing run-event
vocabulary is required. Runtime cannot UPDATE/DELETE journal data.

PostgreSQL tests deliberately seed a synthetic REPRODUCING checkpoint through the
disposable migration role. They do not prove runtime triage/reproduction stage
dispatch. Tests use synthetic reports and ephemeral TEST_TRANSPORT signing keys,
covering restart, concurrent replay, owner/version/lease denial, hash tampering,
expiry, immutable permissions and audit rollback. M0/M1/M2 remain partial;
production signer/runner admission, actual execution, artifact materialization,
external dispatch/reconciliation and reducer acceptance remain open.
