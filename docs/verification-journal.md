# Durable authenticated verdicts

Migration 0017 adds one append-only private verification result per run. The
runtime database role requires SELECT/INSERT on verification_results, alongside
its existing checkpoint/event/job privileges; it must not receive UPDATE/DELETE
on verdict records. Upgrade with `uv run alembic upgrade head` using the migration
role. Review and archive retained verdicts before a production downgrade, which
drops this table. No production database was migrated by the acceptance tests.

VerificationJournal.accept is an internal trusted-worker boundary, not a model
tool or API write endpoint. It accepts a signed gate envelope, explicit verifier
public-key registry and operator-owned image/configuration identities. It never
accepts a serialized verdict as authority. There is no default producer key or
automatic key discovery and no external requests occur in this adapter.

## Transaction and recovery

The run is locked by authenticated owner. With a live fenced worker lease and
exact version, EvidenceStore's shared evaluator reloads current consent, stage,
manifest and actual artifact bytes, derives expected context from the checkpoint,
and reauthenticates signature/freshness/gate policy using database time. Evaluation
and persistence use the same connection and transaction. A previous independent
evaluation object cannot substitute for this check.

The canonical signed envelope plus calculated verdict is retained with SHA256.
RunState.verification_ref points to that private record; the stage becomes
VERIFIED, NOT_VERIFIED or INCONCLUSIVE according to the authenticated evaluator.
The checkpoint version, STAGE_CHANGED event and completed job lease commit
atomically. A final live-lease check rolls everything back if the fence expires
during validation. There is no automatic patch retry or move to human approval.
The generic transition reducer continues to reject VERIFIED.

Concurrent identical deliveries serialize to one record, version and event.
An exact repeat can reconcile the already committed checkpoint without repeating
signature freshness validation, creating another transition or requiring a lease
that completion already closed. This is historical read-only reconciliation,
not new verification authority. Different envelope/configuration/version, changed
checkpoint, or expired/corrupt record is rejected. No repeat extends retention.

## Private reads

VerificationJournal.get checks owner, 24-hour database read expiry, payload hash,
canonical schema and checkpoint/source/patch/test/manifest bindings. Historical
records are not reinterpreted as fresh approval or execution permission.

GET /api/v1/runs/{run_id}/verification uses existing loopback bearer authentication.
It returns verification_ref, verdict and the bounded gate matrix, with no-store.
It returns neither signatures, raw reports, producer key material nor internal
storage paths. Missing/expired/wrong-owner records return 404; corruption returns
a sanitized integrity error. API consumers can render this alongside existing
manifest/artifact routes and run events. No upload or acceptance route is exposed.

## Remaining trust boundary

A signature authenticates the configured deterministic producer. That producer
must independently validate actual baseline failure, candidate, existing suite,
clean replay, scope/security and assessed runner/image/configuration facts before
signing. The consumer cannot infer those truths from arbitrary artifact text.
Baseline reference presence alone does not prove the baseline. This change does
not deploy or attest a producer, run sandbox code, authorize PR publication,
implement physical purge/encrypted backups, or complete M3's UI/live acceptance.
Tests use tiny synthetic artifacts and ephemeral signing authority, including
REMOTE_SERVICE-shaped fixtures strictly for contract testing.
