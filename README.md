# ReplayForge — Blueprint Package

ReplayForge is an evidence-backed incident-repair platform under implementation
for the Nebius x NVIDIA Global AI Hackathon 2026. Implemented local components
include the PostgreSQL run API/event ledger, Next.js workbench, durable worker
recovery, scoped advisory repair memory and a bounded collection/triage harness.
A fixed checkout fixture has retained real Nemotron/remote fail-then-pass replay
evidence. The general live seven-gate repair pipeline, approval-to-PR flow and
public deployment remain incomplete. Local test success is not production readiness.

Current work and acceptance evidence: [delivery tracker](todo.md),
[M3 acceptance](docs/m3-acceptance.md), [implementation log](docs/implementation-log.md).
Runtime guides: [agent harness](docs/agent-harness.md), [memory](docs/memory.md),
[prepared runner](docs/prepared-nebius-backend.md),
[durable replay collection](docs/replay-collection.md),
[measured receipt producer](docs/receipt-producer.md),
[durable producer recovery](docs/durable-producer.md),
[human approval journal](docs/approval-journal.md). Latest full backend regression
at `9df1ac9`: 1191 passed, zero skips; see the retained report linked in the tracker.


## Local development (PowerShell)

Install Python 3.11/3.12 and uv, then run from this directory:

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m scripts.check_nebius
```

The last command performs no network calls and exits nonzero until a live probe
is explicitly requested. Set NEBIUS_API_KEY securely in your process environment;
do not paste it into commands/history or commit it. `.env.example` documents
supported names; `.env` files are not automatically loaded. Optional
NEBIUS_BASE_URL and NEBIUS_MODEL_PRIMARY select a documented endpoint and an
exact NVIDIA Nemotron catalog ID.

With local PostgreSQL installed, `uv run python -m scripts.test_postgres` prompts
for administrator credentials and runs real integration tests in a disposable
database with separate migration/runtime roles. The full current suite needs the
documented 600-second outer harness override in [durable recovery](docs/durable-producer.md);
individual product/test timeouts remain unchanged. Direct pytest without the
isolated DB does not prove PostgreSQL acceptance. See the
[local database/API guide](docs/postgres-local.md) for behavior and limitations.

After authorizing possible inference charges, run
`uv run python -m scripts.check_nebius --live`. It checks catalog membership,
performs one fixed synthetic completion, and prints safe JSON metadata. Exit 0
means this connectivity probe succeeded; exit 2 means not run or not verified.
It does not prove a repair, runner isolation or hackathon readiness. Offline
tests use HTTPX MockTransport and do not contact Nebius.

The [sandbox smoke guide](docs/sandbox-smoke.md) documents the second M0 client.
`uv run python -m scripts.check_sandbox` is offline by default. Only an explicitly
authorized `--live` run submits potentially billable remote work. This client
accepts an existing approved image UUID and one fixed synthetic probe; it cannot
run arbitrary repository commands or patches. Required production isolation
capabilities remain unverified.

See [initial assessment](docs/implementation-assessment.md) and
[implementation evidence](docs/implementation-log.md) for limitations and next work.

The optional [Tavily research adapter](docs/tavily-research.md) supports bounded
public documentation queries with offline policy tests. Run
`uv run python -m scripts.check_tavily` for a safe offline report. One approved live search and local broker/triage/UI integration are recorded in
[research acceptance](docs/m3-acceptance.md). Search results remain advisory;
a complete live repair investigation is still unverified.

Start reading `project_scope.md` (what to build), `Architecture.md` and `system_design.md` (how it works), `AGENTS.md` (trusted boundaries and agent contracts), `instructions.md` (implementation playbook), `milestones.md` and `todo.md` (dated gates and acceptance). Supplementary documents cover threat modeling, benchmarking, UI, API/contracts, benchmark fixtures, submission and an evidence log.

MVP success: a consented synthetic Python repository incident is reproduced on the original commit, repaired through NVIDIA Nemotron via Nebius Token Factory, verified in a clean isolated execution environment with preserved evidence, then transformed into a GitHub PR only after explicit authorized human approval.

Official rules: https://nebiusglobalaihackathon.devpost.com/rules . Deadline: Oct 30, 2026 at 10 AM PDT (22:30 IST). Use the official site as the final authority. Documentation created October 12, 2026. Recommended license for future code: Apache-2.0 or MIT; a license is not automatically granted by this specification.

The bounded [GitHub publication adapter](docs/github-publication.md) now connects
retained verified evidence and the durable approval journal to exact Git object,
new-branch and draft-PR operations. It is disabled for live writes by default.
Local contract tests cover approval checks and restart discovery; no live PR
acceptance or completed M4 milestone is asserted. Authentication is now implemented in this workstream; approval identity/token
brokerage remains a separate integration boundary.

The [GitHub OAuth/session foundation](docs/github-authentication.md) provides a
separate loopback-only auth app factory, browser-bound one-use PKCE attempts,
encrypted user credentials and opaque PostgreSQL sessions. Existing local API
and web startup remain available. Session-based run/repository authorization and
frontend integration now have local synthetic acceptance. Live browser acceptance
remains pending; this is not an enabled public login deployment.


GitHub-session development now includes owner-scoped run/evidence access,
installation/repository selection, exact-commit consent and login/logout UI.
See [authentication setup and remaining acceptance](docs/github-authentication.md).
Enable it explicitly with `REPLAYFORGE_WEB_AUTH_MODE=github`; local bearer mode remains
available. Real GitHub login and approval-token brokerage are still pending.


In GitHub mode, open `/settings/integrations`: a single installation is selected
for you, repository lists are paged, and the latest source commit is pinned when
you select a repository. Confirm access, connect, then use **Start an investigation**
to open the prefilled incident form. **Manage GitHub access** and **Refresh
repositories** handle installation changes. Filters apply to the current page.
No tests start until an incident is submitted through the admitted workflow.
