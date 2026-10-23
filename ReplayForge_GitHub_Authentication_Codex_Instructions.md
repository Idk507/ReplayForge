# ReplayForge — GitHub Authentication and Authorization Implementation Instructions

**Status:** Implementation specification, not a claim of implementation.  
**Target:** Next.js browser UI + FastAPI backend + PostgreSQL + GitHub App.  
**Constraint:** No Microsoft Entra ID dependency or paid authentication provider required.  
**Priority:** Authentication must be real and auditable before exposing incident execution, artifacts, approval, or pull-request endpoints publicly.

## 1. Architectural recommendation

Use **one GitHub App** with user authorization via the OAuth web application flow and separate GitHub App installation authentication for repository operations. The FastAPI backend is the **confidential OAuth client and session authority**. Next.js is an untrusted presentation client and must never receive GitHub client secrets, app private keys, GitHub user/installation tokens, or backend session signing keys.

**Important distinction:** GitHub user authorization and GitHub App installation are **different actions**. Successful GitHub login does not mean the user is permitted to start work on any repository. The server must independently verify the selected installation, repository membership, user access, ReplayForge ownership/role grants, execution consent, and human approval.

Use a backend-for-frontend-compatible flow: browser navigates to `GET /api/v1/auth/github/start` on FastAPI; FastAPI stores a short-lived OAuth attempt, redirects to GitHub; GitHub calls the registered FastAPI callback; FastAPI exchanges the code server-to-server, calls `GET https://api.github.com/user`, links to an internal user by the **stable numeric GitHub user ID**, creates a server-side opaque session, sets the cookie, and redirects to a **fixed allowlisted** Next.js success page. Next.js calls `/api/v1/auth/me` with browser credentials; API authorization is always server enforced. Where frontend and API are cross-origin in development, configure explicit CORS allowlists and cookie behavior; in production prefer a **same-origin reverse-proxy path** `/api/*` to avoid cross-site cookie fragility. Never place session IDs or OAuth tokens in query strings.

Use GitHub's **Authorization Code Flow with PKCE S256** in addition to high-entropy `state` and a confidential client secret. GitHub documents `code_challenge`, `code_challenge_method=S256`, and `code_verifier` for GitHub App user authorization. Do not confuse GitHub App `app_id` with `client_id`.

Design principle: **Authenticated user → authorized repository installation → explicit run consent → verified evidence → explicit fresh human approval → narrowly scoped GitHub write**.

## 2. GitHub App setup (manual administrator steps)

Create a GitHub App in your personal account: https://github.com/settings/apps/new . Suggested name `ReplayForge AI` if available; set homepage to your actual local or deployed homepage. Register `http://127.0.0.1:8000/api/v1/auth/github/callback` for development if supported by your actual browser/backend configuration, and a separate exact `https://<your-real-domain>/api/v1/auth/github/callback` for production. GitHub supports multiple callback URLs; always send the correct registered `redirect_uri`, and disable wildcard matching unless there is an explicit reviewed requirement. Do not commit a made-up production domain.

Enable **expiring user-to-server access tokens**; use `scope=offline_access` only as documented for requesting expiring GitHub App user tokens/refresh tokens. **Do not request OAuth App scopes such as `repo`, `read:user`, or `user:email` for a GitHub App**; permissions are configured on the GitHub App itself. Device flow should remain disabled for a browser app. Configure the app's installation scope as **Only on this account** for initial development, and select **Only selected repositories** when installing. If the application needs third-party users later, revisit app visibility and onboarding.

Recommended **staged permissions**: start with **Metadata: Read** (mandatory/implicit) and **Contents: Read** to enumerate and inspect selected repositories. When the audited PR flow is ready, raise **Contents: Read & write** for commits/branches and **Pull requests: Read & write** for PR creation. Request no Actions/workflow, Administration, Secrets, Organization administration, or Webhooks permissions without concrete requirements. Reauthorize/update an installation if permissions change. For private repository Git transport, prefer a secure credential broker that never embeds a token in process arguments, remotes, logs, or run-visible workspace.

Set a random webhook secret **only if** enabling webhooks; verify each message against `X-Hub-Signature-256` using HMAC-SHA256 over the unmodified raw request body, constant-time comparison, delivery ID deduplication, event allowlisting, and async processing. If avoiding webhooks initially, recheck installation/repository authorization **live via GitHub API** at each sensitive operation; do not depend on stale database installation lists.

Record `GITHUB_APP_ID` (identifier for app JWT, not client ID), `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, and an app private key as a **secret-manager reference or secret file outside the repository**. Never reveal or paste private-key or client-secret values into Codex, logs, chat, tests, screenshots, or tracked files.

## 3. Threat model and trust boundaries

**Untrusted:** browser, callback query parameters, return URLs, repository names and files, incident text, GitHub webhook payload until signature verified, Tavily results, and generated agent commands. **Privileged:** backend's encrypted credentials, session store, signing/encryption keys, GitHub app JWT and installation-token minting service, authorization and PR services. **Isolated:** worker/sandbox executing untrusted repository code; it must never receive GitHub App credentials, a user session, or a privileged GitHub installation token.

Mandatory controls: HTTPS in production; session cookie `HttpOnly`, `Secure`, `SameSite=Lax` when architecture permits, `Path=/` (or precise path); use `__Host-` cookie prefix in production when the cookie meets prefix constraints. Persist only the cryptographic **hash/HMAC** of an opaque random session ID, with expiration, revoked_at, last_seen, user_id, and optional device metadata. Rotate the session on login, and invalidate on logout. No GitHub access tokens or application session IDs in localStorage/sessionStorage. Enforce CSRF validation for cookie-authenticated state-changing endpoints through synchronizer tokens or robust origin+CSRF token defenses; `SameSite` alone is insufficient. Reject cross-origin unsafe requests not from an explicit trusted origin. Avoid wildcard credentialed CORS.

Define separate login-OAuth attempt storage (`state` hashed, PKCE verifier encrypted or in server-side encrypted attempt, fixed redirect URI, expiry ~5–10 minutes, one-time consumption, browser binding) and authenticated session storage. Protect against login CSRF, replay, token substitution, session fixation, open redirects, and account confusion. Use GitHub numeric `id` as external identity key, never mutable login/email. Do not ask for GitHub email unless needed. Any refresh token must be encrypted at rest, atomically rotated, and never returned to frontend. Treat user refresh failures/revocations as an authorization failure and require reauthentication where necessary.

Rate limit login start/callback, failed auth, repo authorization checks, run creation, token minting, and PR actions. Log structured security events without credentials, OAuth `code`, cookie values, or Authorization headers. Introduce explicit per-environment callback/return URL allowlists and trusted host/proxy configuration.

## 4. Persistence model and migrations

Inspect existing database tables before creating any migration; preserve current users/run/approval schemas. Proposed normalized tables (names are suggestions, avoid duplicates):

`users(id UUID PK, status, created_at, updated_at)`; `external_identities(user_id, provider='github', provider_user_id BIGINT UNIQUE, login_display, avatar_url, linked_at)`; `auth_sessions(id UUID PK, session_secret_hash UNIQUE, user_id FK, expires_at, last_seen, revoked_at, created_at)`; `oauth_attempts(id UUID PK, state_hash UNIQUE, verifier_ciphertext, browser_binding_hash, redirect_uri, expires_at, consumed_at)`; `github_installations(id BIGINT PK, account_id BIGINT, account_login_display, status, updated_at)`; `repository_grants(id, repository_id BIGINT, installation_id BIGINT, internal_user_id, authorization_source, expires_at, revoked_at)`; `security_audit_events(id, actor_id, action, target_type, target_id, decision, reason_code, correlation_id, created_at)`.

If short-lived user-to-server access tokens are needed for enumeration: `github_user_credentials(user_id, access_ciphertext, refresh_ciphertext, expires_at, refresh_expires_at, rotation_version)` with envelope encryption/rotatable keys and database access restricted to authentication components. Installation access tokens should preferably be minted on demand and kept briefly in process memory or an encrypted short-lived store, never durable plaintext.

Link existing `runs`, `artifacts`, `approvals`, and `pull_requests` to the authorized user and repository ID through explicit foreign keys / joins. Migrate safely with uniqueness constraints and indexes; roll forward/rollback in disposable test database.

## 5. Required backend API contracts

All endpoints default to `401` without session except login, callback, live health and optional truly public sanitized demo pages.

`GET /api/v1/auth/github/start`: create one-time state + PKCE verifier and browser-bound attempt, redirect to GitHub. Optionally accept only an enum/allowlisted post-login destination, never arbitrary URL.

`GET /api/v1/auth/github/callback`: reject GitHub `error`, missing params, mismatched/expired/replayed state, wrong browser binding; exchange code from backend using client secret + original code verifier; fetch authenticated `/user`; upsert identity by numeric ID; rotate opaque session; set secure cookie; redirect fixed destination. Do not expose detailed credentials/errors in browser.

`GET /api/v1/auth/me`: return minimal `{id, github_login, avatar_url, roles}`; authenticate with server session, `Cache-Control: no-store`.

`POST /api/v1/auth/logout`: CSRF-protected; revoke the server-side session and expire cookie; idempotent, return 204.

`GET /api/v1/github/installations`: read visible installations through GitHub user access APIs where needed; include only installations the signed-in user is authorized to see; revalidate relevant state.

`GET /api/v1/github/repositories`: show only repositories accessible to BOTH user and selected app installation; do not rely on UI filtering.

`POST /api/v1/repositories/{repo_id}/connect`: record explicit user consent and installation association after a live authorization check.

Existing `POST /api/v1/runs`, `GET /api/v1/runs/{id}`, SSE stream, artifacts, approvals, and PR endpoints: enforce owner/tenant/repository permissions server-side, including IDOR-resistant access checks; SSE auth must use same session (no token in URL). Every denial should be non-leaking and auditable.

`POST /api/v1/runs/{id}/approvals`: require authenticated reviewer with current authority, CSRF token, exact repo/commit/patch hash, short expiration, one-time decision, concurrency-safe compare-and-swap. For sensitive PR writes, apply fresh authentication or a short-lived reauthentication requirement if feasible.

`POST /api/v1/runs/{id}/pull-request`: verify one-use approval, current GitHub installation access, repo/branch policy, patch digest, idempotency key, and verified evidence manifest immediately before minting narrowly scoped installation token and writing remotely. Never merge or deploy automatically.

## 6. GitHub authorization and token strategy

**GitHub App identity:** server signs a short-lived app JWT with private key, solely to obtain an installation token. **Installation token:** mint for selected `repository_ids` and reduced `permissions` on demand; GitHub installation tokens expire after 1 hour. Avoid caching beyond expiration and never make installation token accessible to an agent's sandbox. **User access token:** primarily for discovering user-visible installations/repositories and account identity; GitHub App user tokens are bounded by intersection of user rights and app permissions. If app activity must be attributed to a user, explicitly consider user-to-server token instead of installation token for that operation.

Always check consent and repository authorization in **ReplayForge DB**, and confirm selected installation repository availability with GitHub for sensitive work. Do not equate `installation_id` supplied by a browser with ownership. Require selected repo/commit to be tied to an authorization record. A user who authenticated previously but lost GitHub access must not be able to start new runs or approve GitHub writes.

On webhook `installation.deleted`, `installation_repositories.removed`, or `github_app_authorization.revoked`, mark corresponding records invalid, invalidate or restrict affected privileges, and prevent new operations. On startup without webhooks, perform live checks before each privileged action, not only at sign-in. Stop or reauthorize when GitHub says 401/403/404; do not bypass denial with broader credentials.

## 7. Frontend UX

`/login`: prominent **Continue with GitHub** button; never request personal access tokens. `/auth/success`: restore app state by fetching `/api/v1/auth/me`, no tokens in URL. `/dashboard`: user's authorized projects and runs only. `/settings/integrations`: show GitHub identity, selected installations, connected repositories, permission explanations, disconnect/sign-out controls. `/runs/[id]`: protected evidence and event stream; approval controls only for authorized reviewers. Show explicit user-friendly states for GitHub access denied, installation missing, expired token, revoked access, invalid callback, CSRF rejection and reauthentication requirement. Keep all authorization enforced on server, regardless of UI gating.

## 8. Codex execution requirements

Codex MUST first read `AGENTS.md`, `Architecture.md`, `system_design.md`, `project_scope.md`, `instructions.md`, `milestones.md`, `todo.md`, and all existing authentication/API/database code. **Do not rewrite working existing auth or remove its tests without a justified migration.** Create an ADR under `docs/adr/` choosing GitHub App user flow plus separate installation-based repository access. Capture existing auth constraints, database migrations and threat model. Use maintained packages (e.g., `httpx`, `pydantic`, suitable OAuth client, `cryptography`) and inspect their installed versions/APIs; do not copy unverified imports. Use precise types, docstrings, bounded timeouts and meaningful error handling. Preserve Windows PowerShell + `uv` development workflow.

Implement in **vertical slices**, each with acceptance gates and tests: (A) settings/secret injection and offline mocked GitHub client; (B) OAuth state/PKCE callback plus session creation; (C) `/auth/me`, logout, CSRF protection, session ownership; (D) GitHub installation discovery and repository grants; (E) enforcement on existing runs, SSE, artifacts; (F) approval and separate GitHub App installation write-token brokerage; (G) optional verified webhooks; (H) frontend integration and live manual smoke test. No cloud or remote side effects without explicit user approval. Finish each slice by running `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy` or repository-equivalent commands; update `todo.md` only with test-backed completed tasks. Avoid claiming live integration success without authorized credentials and executed calls.

## 9. Acceptance tests (must be automated wherever possible)

**OAuth:** state missing/mismatch/replay/expired; PKCE verifier mismatch; wrong callback and untrusted redirect; login denied by user; code exchange 400/401/429/5xx/timeouts; GitHub `/user` missing ID or wrong schema; two independent browser sessions; OAuth attempt browser binding; existing GitHub user relogin maps same internal ID; renamed GitHub login does not create a new user.

**Sessions:** missing/invalid/expired/revoked cookie; logout invalidates session; fixation prevention; cookie flags; CSRF missing/invalid tokens on every unsafe endpoint; Origin violation; explicit CORS; GET `/me` no-store; no token leaked in URL/body/browser storage/logs.

**Isolation:** User A cannot read User B runs, artifacts, SSE, repositories, approvals, or PRs by substituting identifiers. Anonymous user cannot start runs or execute tools. User logged in but without app installation cannot connect repo. Revoked installation/repository access denies starting runs and PRs. Wrong repo ID / installation ID association denied. Webhook forged signature rejected; duplicate delivery idempotent if implemented.

**Approval/PR:** reviewer cannot approve unverified run; approval bound to hash and original commit; changed patch invalidates approval; expired or reused approval rejected; concurrent PR requests create at most one PR; missing write permissions rejected; never merges/deploys; GitHub API 403/409/422 handled with accurate state and audit evidence.

**Operational:** fully mocked provider tests run in CI without GitHub secrets; opt-in live smoke test confirms actual login, session, selected-repository listing, and logout; no GitHub writes in smoke test. Security event logs include actor/action/decision/time but redact tokens. A separate explicitly approved integration test can create a PR in a disposable demonstration repository when full verification pipeline is available.

## 10. Definition of Done

The milestone is finished only when a real browser can sign in with GitHub, obtain an opaque server-side ReplayForge session, access `/auth/me`, see **only authorized repositories**, and sign out; access to all existing private run/SSE/artifact endpoints is enforced; GitHub login cannot authorize writes; automated OAuth, CSRF, session, IDOR, permission-revocation and API error tests pass. Code and documentation must match observed behavior. Mark GitHub App installation write workflow separately if it is not yet implemented.

## 11. Exact master Codex prompt

> You are implementing ReplayForge GitHub authentication within the existing repository, not starting a new application. Read all project Markdown specifications and audit existing auth, API, PostgreSQL and GitHub code before editing. Treat this entire document as the implementation specification. Use GitHub App OAuth web application authorization code flow with PKCE S256, one-time state, backend-only client secret, stable GitHub numeric identity, opaque PostgreSQL-backed server sessions, secure cookies, logout, CSRF, and proper cross-origin protections. Maintain strict separation between user login tokens and short-lived repo-scoped installation tokens; never equate login with installation authorization. Add least-privilege GitHub App installation discovery, per-repository grants and server-side authorization on existing run, SSE, artifact, approval and PR routes. Implement no privileged execution from an unauthenticated or unauthorized request. Preserve existing code, test with mocked GitHub first, never invent successful live results or perform remote writes without approval, and update todo.md and an ADR after verified changes. Start now: assess repository, state implementation plan briefly, then implement the first fully testable vertical slice in the same session.

## 12. Official sources (verify again immediately before coding)

- GitHub App user authorization and PKCE: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
- GitHub Login tutorial: https://docs.github.com/en/apps/creating-github-apps/writing-code-for-a-github-app/building-a-login-with-github-button-with-a-github-app
- Refresh tokens: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
- GitHub App permission model: https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app
- Authentication types: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/about-authentication-with-a-github-app
- Installation token scoping: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
- Callback URLs: https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-user-authorization-callback-url
- Webhook signature verification: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- GitHub App security: https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/best-practices-for-creating-a-github-app
- OWASP Authentication Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP CSRF Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
