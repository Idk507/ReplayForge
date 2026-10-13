# Repository admission policy

RepositoryGrant is a trusted server-side policy input, not a public registration
endpoint or authentication credential. It binds operator/repository UUIDs to a
canonical GitHub HTTPS URL, up to 32 exact commits and a closed vocabulary of
approved paths. admit requires identity/commit consent and separately approved
private access. Denials return no approved paths or tool authority. The current
API now resolves PostgreSQL grants (migration 0002) and checks exact commit
consent as well as its configured repository UUID list. Runtime has SELECT only
on repository_grants. register_repository is operator-only internal code, not
a public endpoint. Revocation and installation provisioning remain pending.

allowed_entry checks lexical Git-tree path/mode admission only, after a successful
scope decision. It rejects symlinks, submodules, traversal, absolute paths,
Windows reserved devices, alternate streams, encoded/control characters and
selected secret-file names. Directory rules end in / and match component
boundaries. Executable file mode allows reading text, never execution.

This is not a filesystem safe-reader or snapshot proof. The future broker must
validate every ancestor's tree mode, reject case/path collisions, verify blob/tree
identity against the pinned commit, enforce byte limits and avoid host symlinks,
junctions and extraction. A URL alone does not establish repository visibility,
ownership or permission. Scope grants must be persisted and authenticated before
network access. No repository fetch, Git subprocess, archive extraction or code
execution is implemented here.

Real snapshot acceptance needs an explicitly approved repository/commit and
GitHub installation scope. M0 live sandbox execution still requires separate
charge authorization. Provider activation and offline configuration reports are
user-supplied access evidence, not independently observed execution/isolation.

## Bounded object reader

services.repository.snapshot verifies typed Git SHA-1 commit/tree/blob IDs and
records SHA256 file identities. SHA-1 checks compatibility identity, not author
authenticity or collision resistance. Trusted source refs remain necessary.
Format reference: https://git-scm.com/book/en/v2/Git-Internals-Git-Objects .

It traverses relevant approved directories only and checks ancestor modes/paths,
case collisions, malformed trees, binary/LFS content, links and submodules.
Limits: 128 object reads, 64 KiB per object, 2 MiB total, 512 observed entries,
depth 16. Transport must enforce timeouts/size before allocation. No host paths,
Git processes, checkout, extraction or code execution are used.

The snapshot holds the approved subset in read-only memory; read_file requires
an exact path and byte cap. Raw bytes remain untrusted and need redaction before
model use. Tests use an explicitly synthetic adapter with real-format objects.
Production ObjectSource, authenticated fetch and persistent snapshots remain
unimplemented; this is not live GitHub acceptance evidence.
