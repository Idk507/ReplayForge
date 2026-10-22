# Durable human approval and publication intent

`services.approval.journal.ApprovalJournal` supplies the persisted part of M4's
approval boundary. It implements no login/session, public endpoint or GitHub
write. Authentication stays with its existing owner. This journal MUST receive
`ReviewPermission` from trusted independently authenticated human identity and
current repository membership checks on every call. Browser/model JSON cannot
establish that permission. HTTP integration still requires CSRF/session handling.

## Review

The trusted operator configures an allowed target branch. The human request must
match the current run/repository/owner, immutable source commit, exact patch
SHA256, accepted verification reference and policy. The journal locks the run,
requires unexpired accepted VERIFIED evidence, rehashes the retained artifact
bundle, and checks current repository consent. NOT_VERIFIED/INCONCLUSIVE cannot
enter review. A model statement or caller-provided verdict is not accepted.

One immutable `HumanDecision` per run retains reviewer ID, approve/reject, nonce,
exact binding, database time and expiry. TTL is at most 15 minutes and cannot
outlive the underlying verification/evidence. Decision, checkpoint and audit
commit atomically. Approve enters AWAITING_APPROVAL; reject closes the run.
An exact duplicate review returns the original record without renewing time or
adding events. Changed nonce/reviewer/patch/source/target/version is denied.

## Claim

A publication claim rechecks the current identity/permission, consent, source,
patch, target, evidence integrity and approval TTL. Rejected, cancelled, expired,
changed or missing decisions cannot be consumed. One immutable intent consumes
that approval nonce and moves AWAITING_APPROVAL to PR_CREATING atomically.
The stable external UUID and deterministic `replayforge/<run>-<patch>` head
branch are retained for a future publisher. Repeated/concurrent claims reconcile
the same intent and never create another consumption or select a protected
branch. This reconciliation does not extend TTL or create a new approval.

`PublicationIntent.can_authorize_publication` remains false: serializing a record
is not a transferable capability. A publisher must recheck the durable approval
with current authenticated permission immediately before any privileged action.
No GitHub client, branch, commit, PR, merge or deployment is invoked here.

## Historical memory

Approving/rejecting a run changes its workflow stage, not its original verifier
outcome. Memory recall now verifies the immutable review/claim provenance before
using a historical VERIFIED lesson from a later stage. It preserves original
source/evidence expiry and does not recall reviewer permission as authority.
Forged later-stage checkpoints without those records fail closed. Cancelled
histories remain rejected by the existing memory policy.

## Storage and evidence

Migration 0023 adds `approval_decisions` and `publication_intents`. Runtime needs
SELECT/INSERT only for these tables, plus the existing controlled checkpoint and
audit writes. The test harness uses restricted roles in disposable databases;
production role separation/deployment remain operator work. No public route was
enabled and no authentication implementation was changed.

Focused real PostgreSQL tests use synthetic verifier signatures, not actual human
consent to publish. `m4-approval-journal-focused.xml`: 21 passed, zero skips,
19.95s, exit 0. Combined review/memory tests: 40 passed, 1150 deselected, 48.19s,
exit 0 (`m4-approval-memory-focused.xml`). New negative provenance test: 1 passed,
1190 deselected, 9.61s (`m4-review-memory-negative.xml`). Module-absent RED and
three real review/memory regression failures are retained losslessly in gzip.

M4 is not complete: authenticated UI/API integration, actual idempotent GitHub
publication/result recovery and hidden evaluation remain open. Claiming
PR_CREATING means only a durable intent exists; it does not claim a PR is open.
