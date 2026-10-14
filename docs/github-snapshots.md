# Bounded GitHub tree and blob acquisition

GitHubReader.acquire first resolves the consented exact commit through the fixed
GitHub API origin. It then fetches non-recursive trees and only approved blobs.
Provider URLs are ignored. No checkout, archive extraction, Git subprocess,
dependency installation or repository execution occurs on the host.

Tree entries are reconstructed in Git byte ordering, with 040000 normalized to
the raw tree mode 40000. The shared snapshot validator checks raw tree/blob SHA1
identities, paths, ancestor modes, collisions, binary/LFS rejection and scope.
Truncated trees, unexpected mode/type combinations, invalid base64, mismatched
blob size or object ID fail closed. Content has independent SHA256 identities.
Denied directories and symlink targets are never fetched.

Limits retain 128 object reads, 64 KiB per decoded object, 2 MiB decoded total,
512 observed entries and depth 16. Each REST tree/blob response is limited to
128 KiB to accommodate base64 for a 64 KiB blob. Commit metadata remains capped
at 64 KiB. An acquisition has a 20-second progress deadline across object reads;
the individual HTTP request timeout can extend that deadline. No automatic
retry/lease renewal occurs. A worker must still fence its eventual commit.

The commit-to-root binding is GitHub HTTPS metadata, not locally reconstructed
raw commit bytes. Snapshot.commit_identity and ContextBundle.commit_identity
retain GITHUB_HTTPS_METADATA; the original load_snapshot path retains
GIT_OBJECT_HASH. Neither value establishes signed authorship or a verified
repair. GitHubSnapshot also retains the resolution's safe trace/time/origin.
All tests here use TEST_TRANSPORT; authenticated live fetch remains unverified.

Later live update: the separate fixed-repository README smoke passed against the
user-authorized Idk507/maf_ds_agent commit. See github-live-smoke.md for evidence
and the broader CLI credential limitation. Synthetic tests remain TEST_TRANSPORT;
the live smoke does not establish production least privilege or full live workflow.

Snapshot and context evidence_origin now preserve TEST_TRANSPORT or REMOTE_SERVICE
from acquisition; legacy input defaults to NOT_RECORDED. See source-worker.md for
the scoped loader and tested API-to-context composition.

New stored context includes commit_identity. Historical canonical artifacts
without that field remain readable as the pre-REST GIT_OBJECT_HASH form, without
rewriting bytes or digests. The legacy comparison strips only this newly added
field; supplied provenance is never dropped. Owner/expiry/hash checks still apply.

Raw snapshots remain ephemeral. Durable source storage, complete provider
provenance artifacts, worker service scheduling and live acceptance remain open.
Token scope must be provisioned separately as Contents:read on the approved
repository. No existing developer credentials were searched or reused.

Official contracts consulted:

- [GitHub trees: non-recursive GET and truncation](https://docs.github.com/en/rest/git/trees#get-a-tree)
- [GitHub blobs: GET content and encoding](https://docs.github.com/en/rest/git/blobs#get-a-blob)

See github-commit-resolver.md for HTTP consent, redirect and credential boundaries.
