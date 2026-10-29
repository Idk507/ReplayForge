# ReplayForge local workbench

Next.js App Router UI for actual owner-scoped run state, intake, cancel, finite
SSE replay, candidate diff, verdict/gate matrix, integrity-checked artifact previews
and advisory documentation citations. A read-only `/evaluation` page shows the
declared benchmark inventory and denominators; it is explicitly unauthenticated
and cannot authorize gates. Missing evidence is explicitly MISSING.
No mock data ships in the application; synthetic HTTP exists only in test files.

## Start locally on Windows

Use Node.js 22 and the repository's Python development environment.

1. Use a separate local development or disposable PostgreSQL database. Never
   point this setup at the intended application database. Apply the repository's
   current Alembic head (0036) with a dedicated migration role, then use a
   separate least-privilege runtime role for the API. Grant the runtime role
   SELECT/INSERT on private artifacts, research_intents/research_reports and
   verification_results, alongside its existing run/event/job privileges. Do
   not grant repository registration,
   artifact UPDATE/DELETE, superuser, createdb or createrole. Operator-owned
   repository grants must already bind the owner and immutable source commits.
2. Configure the Python process environment: REPLAYFORGE_DATABASE_URL (runtime
   connection on loopback), REPLAYFORGE_OWNER_ID (UUID),
   REPLAYFORGE_APPROVED_REPOSITORIES (JSON array of approved UUIDs), and
   REPLAYFORGE_API_TOKEN (a newly generated 32–256-character printable secret).
   Start from the repository root:
   `uv run uvicorn services.api.local:create_from_env --factory --host 127.0.0.1 --port 8000`.
3. In the web server's environment set the same REPLAYFORGE_API_TOKEN and optional
   REPLAYFORGE_API_URL=http://127.0.0.1:8000. These are server-only settings; never
   use NEXT_PUBLIC for credentials. Do not commit environment files or save the
   bearer in browser storage. From apps/web run `npm ci`, `npm run build`, then
   `npm run start`. Open http://127.0.0.1:3000.

Default startup enables no inference, research, sandbox dispatch, approval or PR
publication. Creating a run stores an incident; worker/provider operation is a
separate authorization. This is local development, not public authentication or
an internet-ready deployment. The backend and bridge reject remote clients/origins;
the backend performs owner, repository, digest and version checks independently.
The bridge has a fixed loopback destination, closed path/method vocabulary,
same-origin write checks, response/request limits and no retry of writes.

## Checks and behavior

`npm run lint`, `npm run typecheck`, `npm run build`, `npm audit`.
From the repository root, `uv run python -m scripts.test_postgres --web` creates
task-owned DB/runtime roles, runs fourteen Playwright checks against real local API
and browser processes, then removes its database/roles and stops the API/web
servers. A local PostgreSQL administrator password is prompted securely; no
provider credentials or cloud calls are used. The browser tests currently use
installed Microsoft Edge. Results/screenshots are in apps/web/test-results.
Three UI contracts use synthetic provider data; one replays the retained live
research archive with mocked surrounding HTTP. The create/cancel/reload journey
uses the actual API/database. Bridge and event-parser checks run without HTTP.

Finite SSE pages reconnect with the last validated sequence and bounded backoff.
Disconnection does not clear the cursor or invent progress. The UI retains the
last 500 events; older events remain in the backend ledger. Client responses are
schema-validated. Artifact previews verify SHA256, decode bounded UTF-8 and render
as inert text. Binary previews fail explicitly. Proposal diffs are never executed.
No cost is invented: remaining reservations are shown as limits.

The paused initial dependency install was preserved in ignored
node_modules-incomplete after automatic deletion review rejected cleanup. The
working node_modules and lockfile are separate. This folder is not needed by the
application and must not be committed. Next telemetry is disabled by the launcher.

References: [Next Route Handlers/BFF](https://nextjs.org/docs/app/guides/backend-for-frontend),
[environment variables](https://nextjs.org/docs/app/guides/environment-variables).
