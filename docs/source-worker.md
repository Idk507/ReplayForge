# Scoped GitHub input loader

github_inputs(token, paths) constructs a trusted InputLoader without network I/O.
Compose it as bind_incident(store, github_inputs(...)) and pass that to worker.tick.
The token and selected paths come from operator configuration, never incident
text/model output. No credential discovery, daemon, automatic retry or deployment
is enabled by constructing this loader.

The graph validates persisted caller/commit consent before calling the loader.
bind_incident reads retained owner-scoped input before source I/O. The GitHub
loader rechecks repository/commit and selected path scope, acquires through the
bounded reader and closes its client. Its read token is included among known
secrets for context redaction; up to 31 additional configured secrets are allowed.
Only 1–16 distinct selected paths are accepted. No untrusted code is executed.

Invalid paths/objects/redirects become SOURCE_REJECTED and POLICY_BLOCKED.
Authentication, rate-limit, timeout and service errors become SOURCE_UNAVAILABLE
and INCONCLUSIVE. The graph reducer commits these outcomes with the same lease
fence and terminal job closure as other collection failures. There is no automatic
retry of rejected actions or unavailable reads; after repairing configuration or
service access an operator must submit a new run. Unexpected programming errors
still propagate, rather than being mislabeled as successful collection.

Context persists both commit_identity and evidence_origin. TEST_TRANSPORT remains
explicit through acquisition, collection and storage. Existing canonical context
without one or both added fields is readable, with NOT_RECORDED for absent origin;
historical bytes/digests remain unchanged. These labels do not prove signed
authorship, sandbox isolation, test execution or a verified repair.

The loader now supplies a metadata-only SourceManifest. It commits atomically
with context, a source_manifest_ref checkpoint, the TRIAGING event and job closure.
Owner-scoped manifest readback retains root/tree/blob/SHA256/time/trace/origin
without persisting source bytes. See source-manifests.md. Bounded serve polling
and cooperative stop are documented in collection-worker.md.

Tests use synthetic HTTP responses and disposable real PostgreSQL. They cover
API incident intake, persisted grant admission, worker reconstruction, source
redaction, context readback and terminal failure/no-reclaim behavior. The separate
authorized live README smoke has passed; production least-privilege credentials
and full live worker acceptance remain unverified. Source snapshots are ephemeral
by default. Explicit snapshot_cipher=SnapshotCipher(...) or SnapshotKeyring(...)
enables bounded encrypted subset storage atomically with context and manifest;
see encrypted-snapshots.md. Keyring rotation uses one active write key and
historical decrypt keys. Production key provisioning, purge/backup retention,
deployment egress and production authentication remain pending.
The reader's bounded acquisition can still outlast a
lease under delays; the commit fence rejects stale work without refunding it.
