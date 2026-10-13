# Durable sanitized context artifacts

Migration 0003 stores bounded ContextBundle bytes in PostgreSQL context_artifacts.
This is a narrow sanitized-context adapter, not raw log, snapshot or runner
evidence storage. The trusted worker must redact with the collector first.

store_context revalidates the bundle and sanitized hashes, serializes sorted
compact UTF-8 JSON, caps it at 4096 bytes and computes its SHA256 content ID.
A live lease token, expected version, COLLECTING stage and matching source
commit are required. Artifact insertion, context_refs update, TRIAGING transition
and event commit together. Stale retries must reconcile the checkpoint.
The reducer rejects TRIAGING without a context reference.

context(run, owner, artifact_id) enforces run ownership and expiry, and verifies
checksum/canonical schema. Local authenticated GET
/api/v1/runs/{run_id}/artifacts/{artifact_id} uses the same checks. Other owners,
expired and absent artifacts return 404. Events contain no context content;
checkpoints contain only content IDs.

Runtime requires SELECT/INSERT only and cannot UPDATE/DELETE artifacts. These
are trusted application owner checks, not tenant RLS. Checksums establish
integrity, not signed provenance or protection from a database administrator.

Seven-day database-clock expiry hides reads, but does not delete bytes. Scheduled
purge, configurable retention, backup deletion, encryption-at-rest and general
artifact manifests/listing remain pending. Tests store synthetic sanitized data
in disposable local databases. Graph wiring and end-to-end verification remain
incomplete; M0/M1 are still partial.
