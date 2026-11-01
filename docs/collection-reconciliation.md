# Collection dispatch journal and reconciliation (migration 0037)

Local-development contract; deployment tooling is still pinned to 0036 and refuses 0037.

## Journal states (per fenced claim)
| State | Durable facts | Meaning |
|---|---|---|
| `CLAIMED_NO_INTENT` | claim only | worker died before intent; no request possible |
| `INTENT_NOT_SENT` | `collection_dispatches` row, no `SENT` | died before send; no request initiated |
| `SENT_UNCERTAIN` | `SENT` committed, no outcome | request may have run; outcome unknown |
| `REJECTED` | `REJECTED` | broker returned a definite refusal (no collection effect) |
| `COMPLETED` | `COMPLETED` or run past collection | manifest retained; nothing to reconcile |
| `NOT_RECONCILABLE` | never claimed / terminal / queued | no action |

`dispatch_once` commits the intent, then `SENT` immediately before the POST. The worker
still refuses to re-claim any previously claimed job and never resends.

## Operator actions (`scripts/reconcile_collection.py`, needs `--run`)
Both require an **expired** lease, row locks, a valid operator id and reason, and write an
append-only `collection_reconciliations` row (also for refused attempts, as `BLOCKED`).
- `--list`: expired pre-collection claims with their observed state.
- `--action requeue`: allowed for `CLAIMED_NO_INTENT`, `INTENT_NOT_SENT`, `REJECTED`; for
  `SENT_UNCERTAIN` only with `--accept-read-only-replay`, the recorded dispatch being
  `READ_ONLY_PINNED_COMMIT`, an unchanged run version and no retained context. Requires current
  stored consent; resets the lease (the old claim is fenced) and the next dispatch re-authorizes live.
- `--action fail`: moves the run to `INCONCLUSIVE` (`SOURCE_UNAVAILABLE`), job `done`, history kept.

Environment: `REPLAYFORGE_RECONCILE_DATABASE_URL` (privileged operator role, not the worker's).
Worker role additionally needs SELECT/INSERT on `collection_dispatches` and
`collection_dispatch_events`. Tests: `tests/integration/test_collection_reconciliation.py` (20).
Limits: replay safety rests on collection being a read of one immutable commit with lease
fencing; the collection API has no idempotency key yet.
