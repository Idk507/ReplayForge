# GitHub authentication and repository sessions

Foundation implementation 51e4343; session/repository continuation 2026-10-23. This is an internal loopback-only application
factory with offline provider tests and real disposable PostgreSQL tests.
**It is not a completed public authentication deployment or live login acceptance.**

## Implemented interfaces

- services.auth.settings.GitHubAuthSettings accepts explicit client ID/secret and
  exact API/web origins; construction reads no environment and performs no I/O.
  Production origins must be HTTPS; explicit local development uses 127.0.0.1
  HTTP origins with ports. Callback and return destinations are fixed suffixes,
  never arbitrary request URLs. Production and local cookies have separate names.
- GitHubAuthClient builds S256-PKCE authorization URLs, exchanges a code in a POST
  form body, and reads /user with an Authorization header. Client secret, verifier
  and access token never appear in query strings. Client ID is not an App ID.
  It requests offline_access for expiring credentials, never repo/email scopes.
  Numeric IDs, display login/avatar and expiring token fields are validated.
  User email and unrelated fields are discarded. Tokens are opaque SecretStrs.
- Provider calls require explicit allow_live on the trusted client instance;
  construction and authorize_url are offline. MockTransport always has TEST
  provenance. HTTP has fixed provider origins, no redirects/proxy inheritance/
  automatic retry, bounded timeouts and a 64 KiB response cap. Errors contain
  fixed codes rather than provider bodies. No installation token or PR call exists.
- AuthVault receives independent 32-byte encryption and HMAC keys from the trusted
  host. AES-GCM envelopes bind purpose and record identity; version v1 is
  authenticated. Hash domains separate browser, state, sessions, CSRF and rate
  buckets. No key is discovered or hardcoded by the application.
- AuthJournal persists browser-bound, one-use OAuth attempts. State and browser
  binding are HMACs, the verifier is encrypted, and attempts expire after ten
  minutes. consume commits a claim BEFORE provider exchange. Unknown exchange
  outcomes require a fresh login; replay cannot produce another session.
- finish atomically links GitHub numeric identity to a new/existing internal user,
  updates encrypted user credentials, consumes the claim and issues a new opaque
  session. Same-account renames retain UUID; different numeric IDs do not inherit
  legacy run owners even if display names match. Disabled users cannot log in.
- Session secrets are random 256-bit values, stored only as HMACs, and expire no
  later than one hour or the received token lifetime. Login rotates the browser's
  previous valid session. Revoked/expired/unknown/test-origin sessions are denied
  on reads. Logout revokes one session and repeated valid logout is harmless.
- Login start/callback have durable peer/action rate buckets (ten per minute).
  Auth security events append actor/action/decision/reason/correlation/time,
  with no OAuth codes, cookies, tokens or source logs. CSRF/session/OAuth denials handled
  by the app use safe reason codes. Events are INSERT/SELECT-only to runtime.

## HTTP factory and boundary

create_auth_app(journal, settings, trusted_client_factory) exposes only:
GET /api/v1/auth/github/start, GET /api/v1/auth/github/callback,
GET /api/v1/auth/me, POST /api/v1/auth/logout.
It retains LocalBoundary, exact configured Host/scheme, bounded query/body input,
explicit credentialed CORS, no-store/no-referrer/nosniff response headers and
closed callback query fields. Invalid callbacks are not reflected in errors.

Production cookie shape is __Host-rf_session with Secure, HttpOnly, SameSite=Lax,
Path=/ and no Domain. Local HTTP intentionally uses rf_dev_session without
Secure. OAuth browser binding uses the equivalent distinct cookie. /me returns
minimal internal/GitHub identity, empty roles and a session-bound CSRF token;
that token is not the opaque session ID and grants no reviewer permission.
Logout requires both trusted Origin and exact CSRF token. The separate composed
session API applies these checks to every unsafe core API operation.
The raw session value is used only in Set-Cookie and trusted private custody.

The factory takes trusted Python dependencies; there is no public startup or
request-supplied provider. allow_test is an explicit test-only
constructor input and defaults false; do not enable it in deployment. The existing
LocalAuth API and shared-bearer web bridge remain unchanged and local-only.
They remain available in local mode. Explicit GitHub mode composes session identity
with the same owner-filtered API and replaces bearer injection with cookie forwarding.

## Migration and operation

Migration 0025 creates auth_users, auth_identities, auth_oauth_attempts,
auth_credentials, auth_sessions, auth_security_events and auth_rate_limits.
Existing users/run owners and repository_grants are not backfilled or rewritten.
Apply only through the existing migration authority. A dedicated auth runtime
role needs SELECT/INSERT/UPDATE on its mutable auth tables and SELECT/INSERT only
on auth_security_events. Execution workers must not receive auth table access or
vault keys. The disposable test role combines permissions for tests only; it is
not a deployment-role prescription. No deployed schema or role was changed.

Keep separate encryption/HMAC keys outside source and inject SecretBytes into
AuthVault. Keyring rotation, expiry cleanup, deployed secret provisioning and
refresh brokerage remain open. Replacing the vault keys invalidates unfinished
attempts/session lookups and makes older encrypted credentials unreadable; do not
silently replace keys and claim transparent rotation. No refresh token is reused
by this foundation; expiry currently requires reauthentication. User credentials
remain encrypted after session logout; production retention/purge policy is pending.

The trusted ASGI server/reverse proxy must disable or redact callback query access
logging (OAuth code/state arrive there), enforce exact forwarded-host/scheme trust
and avoid logging cookies/Authorization. No such public startup was enabled.
Rate accounting currently uses the actual loopback peer; a public reverse-proxy
identity/rate design must be tested before removing LocalBoundary.

## Session/repository continuation

`services.auth.access.create_session_app` composes login routes and the existing
run/SSE/evidence API. Every private run route retains its existing owner-filtered
lookup and additionally checks current user/installation repository access. No
bearer fallback exists in this mode. `/me` roles remain empty; browser identity
is not human review authority.

GET `/api/v1/github/installations` and `/api/v1/github/repositories?installation_id=...`
use user tokens and require current app/user visibility. Each list is bounded to
50 records/10 pages; incomplete/changing lists fail explicitly. POST
`/api/v1/repositories/{numeric_repository_id}/connect` requires matching strict
numeric body IDs, an exact source SHA, execution consent, and separate private
repository consent. GitHub confirms the commit before an immutable UUID execution
grant is created. No existing operator ownership is adopted. Idempotent reconnect
requires identical bindings; a different pinned commit creates another grant.

POST `/api/v1/repositories/{internal_UUID}/disconnect` requires owner session,
Origin and CSRF; atomically revokes the connection and clears execution consent.
It is idempotent and works without GitHub availability. Reconnect requires fresh
provider checks and creates a new grant. This is currently an API-only action.
Fresh provider denial blocks API use, but does not yet automatically mirror
revocation to stored grants. Already running remote compute is not cancelled by
disconnect; existing worker consent checks apply at their admitted boundaries.
No webhook revocation/continuous membership watcher is claimed.

Next.js `REPLAYFORGE_WEB_AUTH_MODE=github` enables login, callback landing, session gate,
identity/logout and installation/repository connection pages. Only the local
session cookie reaches the API; the shared local bearer is never substituted.
CSRF is held in memory, never browser storage. Anonymous/expired sessions unmount
private UI. The connected repository/commit prefill the existing workbench.
Browser tests use clearly synthetic identities/provider data, not real login.

## Explicit local startup

Keep the current local bearer startup for existing development. Session mode uses:

```powershell
uv run --env-file .env uvicorn services.auth.local:create_from_env --factory --host 127.0.0.1 --port 8000 --no-access-log
```

The factory reads only explicit process environment (the command above explicitly
loads `.env`). Set `REPLAYFORGE_DATABASE_URL` to the restricted execution role and
`REPLAYFORGE_AUTH_DATABASE_URL` to a distinct restricted auth role on the same
local database. Migration 0026 adds `auth_repository_connections`. Apply migrations
with migration authority, never either runtime role. Startup rejects administrator,
role-creating/database-creating, auth-table-owner-member and combined test roles.

The auth role needs SELECT/INSERT/UPDATE on mutable auth tables including
connections, SELECT/INSERT on auth_security_events, and SELECT/INSERT/UPDATE on
repository_grants (UPDATE only for owner-scoped disconnect). It must not INSERT
runs. The execution role retains its existing grants and must have no SELECT on
auth_credentials, no INSERT/UPDATE on repository_grants, and no vault keys.
Role provisioning is an operator step; no real deployment roles were modified.

Set GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET and independent random 32-byte keys
encoded as 64 hex characters in REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX and
REPLAYFORGE_AUTH_HMAC_KEY_HEX. Keep keys outside source; fixed example/test keys
are never deployment keys. API/web origins default to http://127.0.0.1:8000 and
http://127.0.0.1:3000. Set REPLAYFORGE_AUTH_ENABLE_GITHUB=true only for explicitly
authorized actual OAuth/read-only calls. Default false makes no provider calls.
Set REPLAYFORGE_WEB_AUTH_MODE=github and REPLAYFORGE_API_URL=http://127.0.0.1:8000 for Next.js.
Register the exact callback http://127.0.0.1:8000/api/v1/auth/github/callback.
Use 127.0.0.1 consistently; do not mix localhost cookies/origins.

## Remaining before authentication is complete

- Real separately configured GitHub App browser login/list/logout acceptance,
  with selected tiny repository and independent human consent. No real credential
  has been used for this implementation. Actual separated-role startup acceptance
  remains pending; config and disposable API integration are tested separately.
- Session-derived current reviewer permission, exact ApprovalJournal HTTP binding,
  and reduced installation-token brokerage for the existing publisher (plan E).
  Login/connection do not authorize a PR. No public approval/write route is mounted.
- Fenced refresh, keyring rotation, retention/purge and production secret provisioning.
  Expired access tokens currently require reauthentication; no blind refresh replay.
- Public HTTPS/proxy/rate deployment, two real browser users and complete actual
  browser-to-PostgreSQL-to-GitHub acceptance. The shipped factory remains loopback.

Test evidence is recorded in docs/implementation-log.md and the corresponding
checks JSON. These local contracts do not close M3/M4 or enable live publication.

## Official references verified before implementation

- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app


Live update2026-10-23: actual Idk507 login/logout and durable session revocation
passed. Standard optional OAuth iss is accepted only for exact GitHub issuer
https://github.com/login/oauth (RFC9207); duplicate/unknown/wrong issuer denied.
Installation discovery succeeded with no visible installations; actual repository
access acceptance remains pending App installation. Earlier no-live statements
describe historical foundation evidence; see auth-live-login-checks.json.


## Large-account onboarding contract - 2026-10-24

Live diagnosis after the user installed/granted access found367 visible GitHub
repositories. The old response total_count<=50 validator rejected that legitimate
account; mapping provider/schema failures to401 caused the web session gate to
show login again. This was a product defect, not a need for broader permissions.

GET /api/v1/github/repositories now accepts optional page1..20000. Explicit page
mode fetches exactly one provider page of at most50 repositories and524288 response
bytes, with the existing10-second HTTP deadline. Total count is bounded at one
million, and partial/duplicate records fail closed. Returned pages are deliberately
partial, not a claim of complete enumeration. Disabled/unreadable entries cannot
be selected/admitted. Omitted page retains the old bounded enumeration contract.
Installation enumeration retains its existing50-record cap. No automatic full
account crawl is introduced. The UI filters only the displayed page, with Next/
Previous and explicit refresh; exactly full final pages may lead to an empty page.

GET /api/v1/github/head requires installation_id,repository_id,page. It verifies
current user/app membership for that page, then reads the latest default-branch
commit metadata (commits?per_page=1), without code/files/test execution. The browser
pins the returned immutable SHA; Advanced can override with another exact SHA.
Connection independently verifies that exact commit before granting consent.

POST connection accepts optional strict repository_page. Migration0027 retains
that locator for fresh page-membership checks during every later private access.
This is a lookup hint, never authority: wrong page/repository/install still deny.
If provider ordering changes, access fails closed until the user refreshes and
reconnects the same source. Successful fresh reconnect can update only the page
locator; existing source/private/owner/installation/provenance constraints remain.
NULL legacy locators retain their original bounded check; connecting afresh upgrades
it after actual current membership verification. No owner/grant migration occurs.

Provider/list/response failures return502, rate exhaustion429, current repository
and CSRF denials403; actual missing/expired local sessions remain401. Unknown OAuth
exchange failure is502 then replay401, preserving irreversible attempt consumption.
The UI no longer signs out for a repository discovery error. Separate installation
and repository fetch completion cannot erase an unrelated discovery error.

The UI auto-selects a sole installation, displays loading/empty/error states,
automatically pins HEAD, keeps explicit private/execution consent, and opens the
prefilled incident form after the connected CTA. It never automatically starts
execution or publishes. GITHUB_APP_SLUG (public validated slug) supplies the fixed
GitHub Manage access link; set this on the server, not from request input. App
registration/secrets/DB roles are one-time operator setup, not end-user tasks.

A browser policy rejected automated inspection of the user's existing integrations
tab during final acceptance. No alternate browser, raw CDP, or indirect UI fetch
was used to bypass that denial. Synthetic browser tests and disposable PostgreSQL
checks continue independently. Live user-tab acceptance must be performed by the
user or through normally admitted browser access later. No live repository connect,
execution, GitHub write or cloud charge is asserted for this continuation.

Primary references checked before implementation:
https://docs.github.com/en/rest/apps/installations
https://docs.github.com/en/rest/repos/commits#list-commits


Picker update2026-10-24 (a6250b2): native repository popup replaced by searchable
radio rows; Retry pageN and no-match guidance added. Selecting a different repo
or source resets explicit consent. Page2 selection/connect covered by browser
regression. Actual queued job had zero worker attempts; worker/source-broker
integration is still required for any workbench repair progression.
