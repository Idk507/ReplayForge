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
signed reports may be preserved for audit; retention alone cannot authorize patching.
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
external dispatch/reconciliation remain open.

## Authenticated baseline acceptance (19 October 2026)

`BaselineJournal.accept(lease, owner, execution_id, junit, verifier,
expected_version=...)` is now the trusted reducer entry point. It locks the
owner checkpoint and live job lease; requires the action, matching command
reservation and unexpired retained receipt; checks current consent and every
source/test/run binding; and revalidates signature, freshness and actual JUnit
bytes using the current configured trusted keys. TEST_TRANSPORT is rejected
even when its signature verifies. The caller cannot supply a baseline hash.

A behavioral failure atomically binds the retained receipt digest and advances
REPRODUCING to PATCHING with one version increment and audit event. Invalid
reproduction closes INCONCLUSIVE with INVALID_REPRODUCTION and completes the
job lease. Audit failure rolls back checkpoint changes. After a successful
commit, recovery reads the checkpoint and proceeds to the existing patch
controller; it does not reaccept or resubmit the baseline. Acceptance requires
a still-fresh receipt; an old retained record alone cannot revive execution.

Authenticated setup/syntax process outcomes (including pytest exit 2) are
retained as rejected inspections using their actual exit code. They cannot
match a behavioral baseline and close with INVALID_REPRODUCTION. Direct
assertion receipt verification retains its stricter exit-1 requirement.

## Requested execution-time reservation

New baseline dispatch intents atomically reserve one sandbox command and the
immutable plan's full timeout (currently 60 seconds). Migration 0014 retains
that amount as reserved_seconds alongside the action digest and version range.
The action digest already binds the plan and its timeout; a caller cannot
supply a cheaper duration independently. This reserves requested process time,
not measured provider usage or sandbox pricing.

If command count or remaining execution time is insufficient, the transaction
instead commits INCONCLUSIVE/BUDGET_EXHAUSTED and completes the lease. No intent
or partial resource debit survives. Failed budget/audit writes roll back. A
concurrent or restart replay returns the original reservation version and never
charges time twice, even when the original reservation used the last second.

Historical intents have NULL reserved_seconds. Reads/reconciliation preserve
that history without inventing an earlier time debit. Neither legacy nor new
reservation metadata grants execution authority or permits resubmitting an
operation with unknown outcome. Capability assessment, owned operation recovery,
actual elapsed-time/usage reconciliation and cloud authorization remain separate.

The shared reservation transaction is also used by model requests. It commits
only recognized budget exhaustion as a terminal outcome; other budget errors
propagate instead of being mislabeled as exhaustion. No runtime DB grants or
execution command templates were broadened. Migration downgrade removes the
new duration metadata and requires the same audit/data review as other schema
rollbacks.

This adds no signing authority, cloud dispatch or capability waiver. Tests use
ephemeral synthetic signing keys, including REMOTE_SERVICE-shaped contract
inputs; they are explicitly not evidence that a production signer exists.
