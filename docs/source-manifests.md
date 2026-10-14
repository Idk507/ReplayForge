# Durable source provenance manifests

Migration 0005 adds source_manifests. This stores original source file identities
and provider acquisition metadata, not raw code, prompts, logs or credentials.
SourceManifest includes repository/source commit, root tree, observed UTC-aware
time, acquisition trace, evidence origin, policy version and up to 128 unique
sorted file entries (path, Git blob ID, SHA256, byte count). Serialized canonical
JSON is capped at 64 KiB. Raw commit verification is always false for this
GitHub HTTPS metadata contract. The manifest is not a repair verdict.

The GitHub loader supplies a manifest alongside its ephemeral snapshot. Only
the collection graph reducer persists it. store_context validates matching
repository/commit, origin, commit identity and original SHA256 for each included
context file. Persisted owner consent and allowed manifest paths are checked
again within the transaction. Observed TEST_TRANSPORT/REMOTE_SERVICE context
requires a manifest; legacy NOT_RECORDED fixtures remain supported.

Manifest insertion, context insertion, source_manifest_ref/context_refs checkpoint
updates, TRIAGING event and job completion commit together. Failure rolls back
all of them. Only a live lease and expected version may commit. One manifest is
allowed per run; repeated completed invocation reconciles its checkpoint rather
than inserting another observation. Raw acquisition before commit may repeat
after worker death; this does not establish exactly-once external reads.

Store.source_manifest and authenticated GET
/api/v1/runs/{run_id}/source-manifests/{artifact_id} enforce owner, expiry, checksum,
schema and canonical bytes. Runtime needs SELECT/INSERT on source_manifests only;
UPDATE/DELETE are prohibited. Seven-day read expiry is not deletion. Deployment
encryption, purge/backup retention and signed provider evidence remain pending.
Checksums do not protect against a privileged database administrator.

This closes the durable metadata provenance gap. Full original file bytes are
still ephemeral: reproducible sandbox packaging, encrypted raw snapshot storage,
artifact signatures and retention must be implemented before claiming durable
raw-source replay. No new live fetch or cloud execution is needed for these tests.
