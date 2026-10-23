# Local GitHub App setup for Idk507

Local preparation completed 2026-10-23. Independent session keys and a dedicated
PostgreSQL database with separate migration/API/auth roles are stored in ignored
.env. No secrets are included here. Startup role checks and anonymous /auth/me
and /runs return401 passed with temporary synthetic App settings. Actual OAuth
has not run. Existing databases and GitHub account settings were not changed.

## Register the App

Open https://github.com/settings/apps/new while signed in as Idk507.

- Name: ReplayForge Idk507 Local (choose another unique name if unavailable).
- Description: Evidence-backed incident repair with explicit repository consent.
- Homepage URL: http://127.0.0.1:3000
- Callback URL: http://127.0.0.1:8000/api/v1/auth/github/callback
- Keep Expire user authorization tokens enabled.
- Leave Request user authorization during installation unchecked for this smoke;
  start authorization using ReplayForge's login button so state/PKCE are created.
- Leave Device Flow off and Setup URL blank.
- Webhook Active: unchecked. No webhook handler is claimed.
- Repository Contents: Read-only. Metadata read access is provided by GitHub.
- Other repository/account/organization permissions: No access.
- Installation availability: Only on this account.

Create the App, then copy its Client ID into GITHUB_CLIENT_ID in the private .env.
Generate a Client Secret and put it into GITHUB_CLIENT_SECRET in that same file.
Never paste secrets in chat or commit .env. A private App key is not needed for
this read-only user-login smoke; installation write brokerage is a later slice.
App ID and Client ID are different values: login requires Client ID.

Install App -> Idk507 -> Only select repositories -> maf_ds_agent. This does not
authorize a PR or repository execution; the subsequent explicit connection/incident
workflow retains its own policy. Do not grant access to all repositories.

## Start and test

Set these existing private .env fields after configuring the App:

REPLAYFORGE_AUTH_ENABLE_GITHUB=true
REPLAYFORGE_WEB_AUTH_MODE=github

The first flag permits necessary GitHub OAuth and read-only API calls. It does not
enable publication. Start the API from the repository root:

```powershell
uv run --env-file .env uvicorn services.auth.local:create_from_env --factory --host 127.0.0.1 --port 8000 --no-access-log
```

In another PowerShell window, start Next.js with explicit root .env loading:

```powershell
uv run --env-file C:/Users/dhanu/Downloads/ReplayForge/.env --directory apps/web npm run dev -- --hostname 127.0.0.1
```

The absolute env path avoids working-directory ambiguity. Keep the file private.
Open http://127.0.0.1:3000/login, Continue with GitHub, authorize as Idk507.
Confirm the page displays Idk507, the selected installation and only permitted
repositories. Sign out; private workbench must disappear. Do not create an incident
or run code for this login-only smoke. Do not record callback URLs with code/state,
cookies, CSRF tokens or provider credentials. Capture only sanitized pass/fail.

No actual OAuth acceptance is claimed until those steps are observed. Do not
replace the local database values with the postgres administrator account.

References checked 2026-10-23:
https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app
https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-user-authorization-callback-url
