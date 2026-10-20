# Private evidence bundles

Migration 0016 adds content-addressed bundles containing an EvidenceManifest and
the exact bounded artifact bytes. Worker-only EvidenceStore.retain accepts at
most 32 artifacts, each at most 64 KiB; the canonical bundle is at most 3 MB.
Binary content is encoded explicitly, never decoded as source or executed.
The producer must sanitize sensitive content before retention.

Retention requires an authenticated owner, a live fenced lease, VERIFYING stage,
current repository consent and the expected checkpoint version. Bundle insertion,
manifest checkpoint reference, version increment and EVIDENCE_RETAINED event
commit atomically. Concurrent identical submissions reconcile one version/event;
conflicting or stale work fails closed. Existing expired content cannot be
resubmitted to refresh its lifetime. No stage advances to VERIFIED and no
verification or publication authority is granted by storage.

Read access joins the run's owner scope and checks server-side expiry. Each read
checks the canonical payload hash, source/run identity and the complete rebuilt
manifest against decoded bytes. Corrupt content is rejected before metadata or
downloads are returned. The runtime DB role needs SELECT/INSERT only; UPDATE and
DELETE remain denied. Metadata and raw bytes stay out of public run events.

## Local development API

The existing loopback boundary and bearer authentication apply to both routes:

- GET /api/v1/runs/{run_id}/evidence/{manifest_ref} returns validated manifest
  metadata with content identities and sizes, without file bytes or storage paths.
- GET /api/v1/runs/{run_id}/evidence/{manifest_ref}/artifacts/{artifact_id} returns
  an artifact found in that same validated bundle. Content is always an attachment
  with application/octet-stream, nosniff and no-store headers. Names are bounded
  ASCII tokens rather than filesystem paths or caller-controlled MIME types.

Wrong owners, missing/expired bundles and unknown artifacts return no content.
Malformed IDs fail schema validation; integrity failures return a safe error
code. No upload endpoint, arbitrary path, inference, sandbox or GitHub action is
introduced. The existing context-artifact routes retain their behavior.

## Retention and rollback limits

The database gives bundles a fixed 24-hour read lifetime. This implements read
expiry, not physical purge or a guarantee of encryption at rest. A separately
privileged bounded maintenance/purge job and backup retention are still needed
before production retention can be considered complete. Read expiry does not
remove the checkpoint's historical manifest reference.

Migration 0016 adds EVIDENCE_RETAINED to the closed event allowlist. Downgrading
drops the bundle table, which requires evidence archival review. The additive
event vocabulary remains recognized on partial rollback so existing immutable
audit records remain valid; a full downgrade to base removes the event table.
Reapplying the migration re-establishes the same closed vocabulary.

The gate verifier's context should bind the version after bundle retention.
Storage does not authenticate a producer or decide whether raw observations
prove a gate. Durable authenticated verdict reduction is now implemented through
the shared transaction boundary (verification-journal.md). Producer deployment,
actual runner capabilities, physical retention and M3 UI acceptance remain open.
Tests use small synthetic content in a disposable local database.
