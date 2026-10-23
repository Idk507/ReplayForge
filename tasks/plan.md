# Implementation plan: GitHub authentication and repository authorization

Status: C/D session/repository and browser implementation locally tested,
2026-10-23. A/B foundations at 51e4343 pass
1277 full local backend tests; live acceptance remains unverified. User has reassigned authentication into this workstream.
Tasks are tracked in root todo.md; this document is the dependency/acceptance plan.
Specification: ReplayForge_GitHub_Authentication_Codex_Instructions.md.

## Existing implementation and reuse

services/api/app.py uses LocalAuth, a server-configured token/owner/repository list,
and LocalBoundary rejects non-loopback peers. All existing owner-filtered Store
reads, run CAS checks and private evidence custody should be preserved.
apps/web/lib/bridge.ts forwards one server-side local bearer and allows only
loopback origins/closed routes. Adding login without replacing this behavior
would still grant every browser the same operator identity.
There are no user/session/OAuth identity tables in migrations 0001-0024.
repository_grants already means immutable execution scope, keyed by UUID and owner;
it must not be replaced with GitHub numeric repository IDs or casually made writable
to a worker. ApprovalJournal/PublicationJournal/GitHubPublisher already supply the
exact review, one-use intent, per-POST recheck and isolated writer integration.

## Decisions

- One GitHub App, user authorization code flow with state and S256 PKCE; backend
  session authority; separate app-installation tokens for repository operations.
- Prefer one origin: reverse proxy /api/v1 to FastAPI and UI pages to Next.js.
  Callback must reach FastAPI directly without logging its query string. Keep the
  existing local bridge explicitly local-only; never expose its shared bearer in
  GitHub-session mode. If a BFF proxy is retained, forward only intended cookies,
  Origin and CSRF metadata, preserve multiple Set-Cookie headers, bound streams,
  and let FastAPI authenticate/authorize. Never inject a shared operator bearer.
- Explicit separate local-development and GitHub-session startup factories. No
  token fallback when a session is absent. Removing the loopback boundary is a
  later, tested deployment decision, never part of adding a login button.
- UUID internal users keyed to unique GitHub numeric identities. Mutable login and
  avatar are display only. Existing owner UUIDs stay isolated: linking legacy
  operator data requires explicit operator migration, never email/login matching.
- New auth repository-access associations map GitHub repository/installation IDs
  to existing internal repository UUIDs and principals. Connected ownership and
  fresh GitHub access are additional requirements; login does not grant execution.
  Initial single-user ownership is sufficient; no invented organization/role UI.
- Opaque random browser sessions; database retains hash/HMAC only. Separate
  one-use browser-bound OAuth attempts retain encrypted verifier. Credentials use
  authenticated encryption with purpose/user/version binding, explicit external
  keys and key IDs; do not reuse source-encryption keys implicitly.
- Secure HttpOnly host-only cookies in HTTPS production, SameSite=Lax plus CSRF
  token and exact Origin checks for unsafe requests. Explicit local HTTP cookie
  names/settings: no __Host- or Secure claim on plain HTTP. No provider credentials
  in browser storage, public exceptions, callback access logs or telemetry.
- Expiring user credentials are encrypted and refresh is fenced by version/claim.
  Persist new credential pair atomically. Unknown token exchange/refresh outcomes
  require reauthentication, never blind replay of a single-use code/token.
- No webhook dependency initially. Verify current relevant user/installation/repo
  access before sensitive operations; 401/403/404 fail closed. Bounded enumeration
  with pagination limits; no silent incomplete-list success.
- Auth service role may update its own narrow auth tables and append security
  events. Workers keep existing read-only execution grants and no auth keys.
  Connecting a repository needs a separately constrained grant-writing boundary;
  do not give the general API/worker migration-owner privileges.
- No new OAuth/session framework or JWT browser session needed. Reuse installed
  HTTPX/Pydantic/Psycopg/cryptography after API inspection. Installation JWT signing
  requires deliberate vetted implementation/dependency choice and offline tests.

## Ordered vertical slices

### A. Configuration and GitHub transport (first implementation slice)

Files: services/auth/settings.py, github.py, auth contracts and focused tests.
Accept: exact configured callbacks/origins and explicit secret injection; bounded
fixed-origin token exchange and /user projection with numeric identity; no I/O at
construction, no redirects, environment proxy inheritance, body/log leakage or
automatic retries of code exchange. MockTransport tests cover PKCE fields,
malformed identity, timeout/auth/rate-limit/server errors. This slice needs no real
GitHub credentials and does not claim working login.

### B. OAuth-to-session with real PostgreSQL custody

Depends A. Add next migration after 0024 for users/external identities, attempts,
encrypted user credentials, sessions and append-only security events. Add start
and callback routes in GitHub mode. Use browser-binding cookie, hashed state,
expiring atomic one-use consume before external exchange; identity link and new
session creation commit together after /user. Rotate/revoke old session. Denied
or uncertain exchange creates no session. Tests: concurrent/replayed/expired state,
wrong browser, wrong callback, denied login, failed exchange and renamed account;
no secret/raw code in public/audit output. Migration round-trip on disposable DB.

### C. Usable browser identity and protected existing API

Depends B. Add /me no-store, CSRF bootstrap, idempotent protected logout. Introduce
minimal typed principal dependency used by existing routes, preserving local mode.
Protect runs/list/cancel, evidence/artifact/patch/research/verdict and SSE by owner
and repository authorization; no query-string session credential. Update bridge
and add minimal login/logout/auth-expired UX together. All unsafe endpoints reject
missing/wrong CSRF or Origin. Test two browsers/users, revoked/expired/fixed cookies,
ID substitution and logout; retain local-mode tests. No public startup yet.

### D. Real authorized repository connection

Depends C. Add bounded user-visible installation/repository discovery and explicit
connect consent. Numeric repository IDs map to existing internal UUID grants;
check BOTH user and installation access server-side, never trust supplied
installation ID. Read credentials only in auth service; fenced refresh and denied
revocation paths. Test wrong installation/repo association, private repo denial,
removed access, rename, reconnect and owner isolation. UI displays only authorized
repositories and meaningful missing-installation/permission states.

### E. Approval and installation token brokerage

Depends D plus existing authentic VERIFIED evidence. Derive ReviewPermission from
current session and current repository permission, not client JSON. Exact patch,
source, target, version, CSRF and one-use approval go through ApprovalJournal.
Mint installation token for precisely selected numeric repository and reduced
Contents/Pull requests permissions only after admitted publication. Recheck access
through existing publisher callback. Test concurrent requests, token expiry,
revocation, changed patch and lost responses using synthetic HTTP, never a live PR.
Browser approval is distinct from login and installation consent.

### F. Integrated browser/live read-only acceptance

Depends C/D (E reported separately). Test rendered login, callback, /me, authorized
repository list, run/evidence access, logout and denied old session. UI lint/type,
production build and Playwright cases plus full backend security regression.
Then separately authorized real GitHub login/list/logout with one selected tiny
repository. Human completes GitHub consent. No sandbox/inference/PR writes in
this smoke. Keep callback queries and credentials out of saved artifacts.

Optional after completion: signed idempotent webhook revocation processing. This
is not a prerequisite when mandatory live authorization checks are implemented.

## Quality and definition of completion

Each implemented slice: focused negative tests, disposable PostgreSQL integration
where relevant, Ruff check/format, strict mypy and repository regression; UI changes
also lint/typecheck/build and browser tests. Validate dependency/secret findings
when dependencies/credential boundaries change. Keep exact commits/commands/exits
in docs/implementation-log.md; update todo only with actual evidence.

Auth is complete only after a real browser signs in, sees only its authorized
repositories, reads private resources with correct isolation and signs out,
plus OAuth/session/CSRF/IDOR/revocation tests pass. Installation write brokerage
and live PR acceptance remain separately tracked. Authentication alone does not
close M3 seven-gate work or M4 evaluation.

## Setup required before live acceptance (not before offline development)

GitHub App registered/installed on selected test repository; app ID and client ID;
client secret supplied through private server configuration (the private-key
file/reference is required later for installation token brokerage); exact browser/API origins and callback chosen together; generated
external auth-encryption/session-HMAC keys and restricted DB roles. Never paste
secrets into chat. No remote registration/config change or paid/write action is
performed by this plan.

## Verified primary references

Checked 2026-10-23:
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app

PKCE S256 is documented for GitHub Apps. offline_access requests expiring user
credentials, not repository permissions; traditional OAuth scopes do not apply.
Installation token responses must be treated as bounded opaque secrets; do not
assume a fixed 40-character format. Scoping repository_ids and permissions must
be explicit rather than inheriting every installation permission.


## Current continuation scope

C/D code composes session identity/current repository access with the existing API;
Next.js login/logout and repository selection are implemented in explicit GitHub
mode. Disconnect revokes stored execution consent atomically. Focused session/
repository/startup tests: 22 passed, auth-session-disconnect-green.xml. Browser
synthetic session suite: 3 passed, auth-session-browser.xml; desktop and mobile
screenshots retained and visually inspected. Full regression: auth-session-final-postgres.xml, 1299 passed, zero skips,
417.46 seconds, exit 0. Implementation 638feb6. E remains open; F real GitHub App acceptance needs
operator configuration and human consent. No private credentials or authenticated
live GitHub requests were used during this continuation.
