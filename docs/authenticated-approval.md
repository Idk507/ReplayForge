# Authenticated review and restricted publication

Implemented2026-10-25. Local synthetic acceptance is distinct from a real approved
GitHub PR and does not close M4. M3 live repair acceptance remains open.

## Human review

The session app optionally installs GET /api/v1/runs/{id}/review and POST
/api/v1/runs/{id}/approval with an operator-selected target branch. The local
bearer app has neither route. Current session owner, repository consent, user
push permission and installation write permissions are checked. POST additionally
requires Origin/CSRF. Browser roles/reviewer IDs cannot supply ReviewPermission.
Initial review is owner-only. The journal checks actual accepted evidence,
source/patch/verification/target/policy binding, version, nonce and TTL.

Review reads preserve the original receipt and publication_version across restart;
they never renew expiry. Approval/rejection retries must use the original payload.
The workbench Patch & risk tab hashes the displayed UTF8 diff before review and
checks run/repository/source/verification bindings. A checkbox records explicit
human review. Approval and draft PR creation are separate actions; no page load
or approval automatically publishes. An unconfirmed POST is not success.

## Installation credentials and publication

services/auth/installation.py signs a short RS256 JWT with an explicitly injected
RSA key of at least2048bits. It requests exactly one numeric repository ID and
contents:write plus pull_requests:write. Response must contain that exact
repository ID/name and those permissions, with at most metadata:read. Extra
permissions, absent scope, malformed/expired tokens, redirects, excessive or
compressed responses fail closed. No automatic mint retry/cache is installed.
The current durable approval callback runs before POST and before token return.
Unknown mint can leave an uncollected provider credential until its natural
expiry; no immediate revocation/deletion is claimed. Tokens remain SecretStr in
the privileged service, never browser/worker/model/event/archive data.

services/auth/publication.py composes session identity, ApprovalJournal,
PublicationJournal, token brokerage and GitHubPublisher at POST
/api/v1/runs/{id}/publication. Session/current write membership/consent/evidence/
exact approval/target are reloaded before every publisher POST. Disabled live
writes cannot consume review. Unknown writes recover by admitted owned branch/PR
discovery, never a new identity. TEST_TRANSPORT cannot become PR_OPENED evidence.
The App key is excluded by *.pem/*.key/*.p12/*.pfx. Local PEM validation proves
format only, not that the key matches the configured GitHub App.

## Operator configuration and remaining acceptance

Keep separate auth/API database roles and existing migration authority. Existing
append-only approval/publication tables from migrations0023/0024 and restricted
grants are required; no deployed schema/grants were changed. Workers receive no
App key, vault or GitHub credentials. Configuration:

- REPLAYFORGE_APPROVAL_TARGET_BRANCH: exact allowed base, e.g.main.
- GITHUB_APP_PRIVATE_KEY_PATH: absolute local unencrypted RSA PEM path.
- REPLAYFORGE_ENABLE_GITHUB_PUBLICATION=true: deliberate publication activation;
  also requires REPLAYFORGE_AUTH_ENABLE_GITHUB=true. Default remains false.

The user-provided key was format-validated without printing it and referenced only
in ignored .env. Publication activation was not changed. Startup performs no
provider request. Keep access logging disabled:

```powershell
uv run --env-file .env uvicorn services.auth.local:create_from_env --factory --host 127.0.0.1 --port 8000 --no-access-log
```

This remains loopback-only, not public deployment acceptance. No live token mint,
PR, merge or cloud call occurred. Authentic seven-gate repair must precede exact
human review and real approved PR acceptance. General materialization/collector
wiring and hidden variation evaluation remain unfinished; no milestone weakened.

## Official references

- [Installation token restriction and expiry](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app).
- [GitHub App JWT algorithm and claims](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app).
- [Nebius instance API](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/instances/spawn-a-new-container-instance). Its request settings do not independently establish all required capability/OCI identity facts.
