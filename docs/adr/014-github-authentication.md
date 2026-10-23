# ADR 014: GitHub user sessions and separate installation authorization

Status: accepted design, 2026-10-23. A/B foundation implemented at 51e4343;
repository/UI integration and live acceptance remain open.

The user has reassigned authentication implementation to this workstream.
Adopt one GitHub App's user authorization web flow with state and S256 PKCE,
PostgreSQL-backed opaque sessions, CSRF and stable numeric external identities.
Use separate least-privilege installation credentials for repository writes.
Login, installation access, explicit execution consent and exact human patch
approval remain distinct boundaries.

Preserve LocalAuth/loopback startup and existing tests as an explicit local mode.
Add GitHub mode through typed principal/authorization dependencies. The current
Next.js shared-bearer bridge must not authenticate GitHub browser sessions as a
local operator. Prefer a same-origin API reverse proxy with FastAPI owning
sessions/cookies and authorization. No public startup until private-route and
cross-user isolation acceptance pass.

Keep existing internal owner/repository UUIDs and immutable execution grants.
Add explicit mappings for GitHub numeric user/repository/installation IDs; do not
silently attach old owner data to a newly authenticated identity. Auth-specific
restricted database access and secret keys are isolated from execution workers.

Reject alternatives: local token as public user auth; login automatically granting
repository access; browser-stored GitHub credentials; JWT sessions that bypass
server revocation; broad installation tokens available to a runner; wholesale
replacement of existing owner/grant checks. Webhooks are optional initially if
current access is checked at each sensitive operation.

Implementation slices, concrete acceptance and setup dependencies: tasks/plan.md.
This ADR extends ADR 012 for a new explicit mode; it does not weaken that local
mode or claim authentication implementation or M3/M4 completion.
