# ReplayForge — Blueprint Package

ReplayForge is an evidence-first autonomous repair platform under implementation for the Nebius x NVIDIA Global AI Hackathon 2026. M0 provides Nebius inference, sandbox and documentation-search probes. Local M1 work includes run contracts, PostgreSQL checkpoints/job leases and an authenticated loopback-only API with SSE replay. UI, agents, repository execution and repair verification are not implemented. Live cloud integrations remain unverified. The target layout is specified in `repository_structure.md`.

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
database with separate migration/runtime roles. See the
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
`uv run python -m scripts.check_tavily` for a safe offline report. Live access
and integration into an authentic agent investigation remain unverified.

Start reading `project_scope.md` (what to build), `Architecture.md` and `system_design.md` (how it works), `AGENTS.md` (trusted boundaries and agent contracts), `instructions.md` (implementation playbook), `milestones.md` and `todo.md` (dated gates and acceptance). Supplementary documents cover threat modeling, benchmarking, UI, API/contracts, benchmark fixtures, submission and an evidence log.

MVP success: a consented synthetic Python repository incident is reproduced on the original commit, repaired through NVIDIA Nemotron via Nebius Token Factory, verified in a clean isolated execution environment with preserved evidence, then transformed into a GitHub PR only after explicit authorized human approval.

Official rules: https://nebiusglobalaihackathon.devpost.com/rules . Deadline: Oct 30, 2026 at 10 AM PDT (22:30 IST). Use the official site as the final authority. Documentation created October 12, 2026. Recommended license for future code: Apache-2.0 or MIT; a license is not automatically granted by this specification.
