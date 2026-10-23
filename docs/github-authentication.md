# GitHub OAuth and session foundation

Implementation 51e4343, 2026-10-23. This is an internal loopback-only application
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
Logout requires both trusted Origin and exact CSRF token. Other unsafe/private
endpoints are not mounted, so these checks cannot accidentally enable execution.
The raw session value is used only in Set-Cookie and trusted private custody.

The factory takes trusted Python dependencies; there is no environment-based
public startup or request-supplied provider. allow_test is an explicit test-only
constructor input and defaults false; do not enable it in deployment. The existing
LocalAuth API and shared-bearer web bridge remain unchanged and local-only.
They do NOT accept these sessions yet. /auth/success UI integration is still open.

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

## Remaining before authentication is complete

1. Wire a typed session principal and current repository checks into all existing
   run/SSE/artifact endpoints, preserving local mode without bearer fallback.
2. Add GitHub installation/repository intersection discovery, explicit connection
   consent and internal UUID associations. Login alone grants no repository scope.
3. Implement fenced credential refresh, key rotation/retention, dedicated deployed
   role/startup configuration and operational failure/rate tests.
4. Connect Next.js login/success/logout/repository UX. Replace the shared-operator
   bridge only in a separately tested session mode; verify two real browser users.
5. Derive ReviewPermission from actual identity/current membership and supply
   reduced installation tokens to the existing publisher. No browser role or
   serialized intent is sufficient. Live PR testing is separately authorized.
6. Perform separately authorized real browser login/list/logout acceptance after
   the GitHub App/client settings are provided. No real credential was used here.

Focused tests: auth-foundation-final-focused.xml; full regression and checksums
are recorded in the implementation log and auth-foundation-checks.json.

## Official references verified before implementation

- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
