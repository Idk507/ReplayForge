# GitHub publication — bounded privileged adapter

Implemented 2026-10-23. This is a locally tested adapter/controller, **not live
GitHub acceptance or M4 completion**. Authentication stays with its assigned owner.

## Trusted integration

`services.orchestrator.publication.publish_approved` accepts a trusted
`permission_loader` and publisher factory. The loader must independently verify
human identity and current membership on every call. Do not populate it from
browser/model JSON. The isolated installation credential belongs only in the
GitHubPublisher control-plane instance, never in a guest, patch or event.
Both controller and HTTP adapter default to refusing live writes. No public route
or automatic harness stage was added. An operator must explicitly authorize the
actual write and supply the current reviewed binding and nonce.

ApprovalJournal consumes/reconciles the same one-use intent. PublicationJournal
loads the exact diff by SHA256 from retained verified evidence and uses the
original review timestamp. No caller can substitute source bytes or choose a
new branch, timestamp or external publication identity after a retry.

Before each POST the controller reloads approval/membership/consent/current
source/patch/verdict/target/version/expiry and checks the repository grant has
not changed. Serialized intent is not reusable authority. Permission loss stops
subsequent writes; it cannot undo an already completed authorized HTTP request.

## Exact Git operations

The adapter reads pinned commit/tree/blob objects over the fixed GitHub API
origin. It verifies tree and blob Git SHA1 identities against actual bytes;
these are Git identities, not SHA256 evidence signatures. It avoids the Contents
API because that API can return symlink targets as files. Modified leaves must
be regular non-executable 100644 files, with tree ancestors and exact allowed
`src/` or `app/` Python paths. Existing tests/workflows cannot be changed.

Only modify-existing, bounded UTF8 LF-terminated source files are supported:
64 KiB per source/result, 25 paths, existing 500 KB/5000 changed-line diff limits.
Hunks apply against exact source lines, without fuzzy matching. New/deleted/
renamed files, binary/CRLF/non-LF-terminated source and larger trees are explicit
unsupported cases. Unicode line separators inside strings remain source text.
No clone, filesystem checkout, shell, package install or source execution occurs.

Local Git object construction computes the expected changed tree and commit
before writes. `git hash-object --stdin` tests independently check the encodings.
The author identity and review timestamp are fixed. The adapter POSTs a tree,
commit with one pinned parent, a new deterministic replayforge branch and a draft
PR. It never force-updates refs, updates the target branch, merges, deletes,
modifies Actions or runs a repository command.

The branch must point to the exact expected commit. Discovery searches all PR
states by exact head/base and durable external-ID marker. PR metadata must match
source/head/repository/target/canonical URL. Closed matching PRs are retained as
closed facts, never reopened automatically. Conflicting branches/unrelated or
multiple PRs fail closed. Target movement stops publication; it requires new
verification/review rather than silent rebasing.

## Recovery and custody

Each attempt has at most 128 HTTP requests and a 60-second aggregate deadline,
5-second request/3-second connect timeout, 256 KiB response limit, no redirects,
environment proxy inheritance, compression or automatic write retries. Unknown
POST outcomes return a sanitized failure. A later explicitly admitted attempt
uses deterministic branch/commit and external-ID PR discovery. Ref/PR 422s are
discovered once, not treated as success without matching remote facts. Git object
writes before a lost ref response may repeat content-addressed objects; no
second branch/PR identity is selected. No automatic broad retry loop is installed.

Migration 0024 adds immutable publication_results with a 24-hour metadata TTL.
Runtime access is SELECT/INSERT only. Result + PUBLICATION_OBSERVED event +
PR_OPENED checkpoint commit atomically. A late observation after cancellation is
retained without reopening the run. Restart reads preserve observation time and
avoid HTTP; they do not renew evidence or approval. Expired approval cannot
perform fresh discovery writes. HTTP MockTransport results are always TEST and
cannot be retained as real PR_OPENED evidence. Database success tests use an
explicit synthetic trusted port; they do not prove provider integration.

Deployment must apply migration and append-only grants through its existing
migration authority. No deployed database, GitHub repository or cloud service
was changed during development. Installation token mint/refresh and public auth
remain the teammate's responsibility. No secrets are discovered automatically.

## Acceptance still required

A real tiny fixture must first have authentic accepted seven-gate evidence and
an independently authenticated review of its exact patch/source/target. Then
explicitly authorize one draft PR on the consented repository, retain the actual
URL/commit/result, and retry discovery to prove exactly one PR. Synthetic tests
cannot replace that exit. Hidden evaluation and full worker/UI wiring also remain
open; M3/M4 readiness is not asserted.

## Primary references

- [GitHub trees](https://docs.github.com/en/rest/git/trees)
- [GitHub commits](https://docs.github.com/en/rest/git/commits)
- [GitHub references](https://docs.github.com/en/rest/git/refs)
- [GitHub pull requests](https://docs.github.com/en/rest/pulls/pulls)
- [Contents symlink behavior](https://docs.github.com/en/rest/repos/contents)
- [Git object encoding](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)

The adapter pins the documented 2026-03-10 REST version. GitHub's actual commit
normalization and installation permissions remain unverified until live acceptance.

Partial tree/commit/branch creation can remain remotely if a later permission
check fails. The adapter deliberately performs no automatic destructive cleanup;
a trusted operator must reconcile partial publication. GitHub writes and the
local database are not one distributed transaction. Ambiguous discovery, altered
remote objects or approval expiry stop the workflow for review rather than
claiming exactly-once live acceptance from synthetic tests.
