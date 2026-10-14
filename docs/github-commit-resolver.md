# Read-only GitHub commit resolution

GitHubReader.resolve accepts a trusted persisted RepositoryGrant, authenticated
caller UUID and exact approved commit. It revalidates consent, ownership and
private access before making one GET to a fixed api.github.com endpoint.
No caller/model URLs, provider-returned URLs, redirects, write methods, automatic
retries, ambient credentials or environment proxies are used. The injected
credential must be provisioned separately with Contents:read on the approved
repository; this client cannot prove that the supplied token has no extra scope.

The response is capped at 64 KiB with 4 KiB application chunks, a 10-second
progress deadline and 3-second connect/5-second HTTP timeouts. In-flight HTTP I/O
may extend the progress deadline by a request timeout. Compression is rejected.
Errors expose stable codes without body/token/request details. Provider commit
messages, identities and returned URLs are discarded. The returned commit must
exactly match the requested approved SHA; root tree SHA is schema validated.

CommitResolution records repository/commit/tree IDs, UTC observation, trace ID,
policy decision and evidence origin. Injected transports always produce
TEST_TRANSPORT. REMOTE_SERVICE means an actual HTTP response, not raw Git object
verification, signed authorship, permission-scope validation or executed tests.
raw_commit_verified is always false. No live resolution was performed here.

This is an independent commit-metadata boundary, not the production ObjectSource
for snapshot.py. The existing raw commit/tree/blob hash validator is unchanged.
GitHub REST structured metadata must not be reserialized and falsely presented
as raw commit bytes. Bounded tree/blob acquisition now exists through acquire;
see github-snapshots.md. Durable snapshots, production worker wiring and
authenticated live acceptance remain open.

Official sources consulted for this slice:

- [GitHub Git commits: GET contract and Contents read permission](https://docs.github.com/en/rest/git/commits#get-a-commit-object)
- [HTTPX client lifecycle and configuration](https://www.python-httpx.org/advanced/clients/)

The request pins the 2026-03-10 API version shown by GitHub's current examples.
Tests use synthetic HTTP responses and no real credentials or repository access.
To establish live acceptance, an operator must provision the exact repository,
commit grant and separate read-only installation credential securely. Do not
paste that credential into chat or use a GitHub PR-writing credential here.
