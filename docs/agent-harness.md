# ReplayForge agent harness

## Source-informed choice — 2026-10-22

Reviewed official resources:

- [Anthropic: effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents):
  explicit progress artifacts, session continuity, incremental work and execution
  checks address forgotten state and premature completion. ReplayForge uses its
  PostgreSQL checkpoints, intents and immutable evidence as the progress record.
- [Anthropic autonomous-coding reference](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding):
  inspected the official README through raw.githubusercontent.com after the HTML
  path was unavailable. Its bounded iteration/resume example informs the loop.
  No source code or SDK was copied or installed.
- [LangChain: anatomy of an agent harness](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness):
  the runtime includes state, tools, orchestration and enforceable controls.
  ReplayForge already implements these through typed stages, brokers and policies.
- [LangChain: improving agents through harness engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering):
  execution-based feedback and trace-driven failure analysis motivate keeping
  deterministic checks and durable events in the loop. Published benchmark gains
  are not evidence of ReplayForge's performance and are not claimed here.

The suitable increment is a fixed-stage scheduler connecting the existing durable
collection and triage paths. Nemotron/Nebius remains the configured provider.
Memory, bounded context, request reservations and verified completion remain in
their existing modules. The references' shell, filesystem and multi-agent features
are not enabled as model tools on the Windows/API host.

## Runtime behavior

`services.orchestrator.harness.serve_harness` alternates TRIAGING and collection
claims when a model and cost policy are configured. It begins with triage to
resume queued work before collecting more input. Each step attempts at most one
claim; 1..1000 steps are permitted. The loop waits only after both queues are idle
(or one collection queue when inference is off), with a finite wait greater than zero and at most 60 seconds. Stop is checked before each step; active operations
finish or obey their existing timeout before shutdown. Exceptions propagate with
no blind retry. CLI signal handlers are restored on success/error.

Collection queues triage atomically. Triage loads current evidence, research and
scoped historical memory, reserves the exact outbound request hash and budgets,
then retains the validated reproduction artifact and completes its job atomically.
Its uncertainty journal prevents duplicate inference after disconnect/restart.
No new scheduler state must be recovered: existing DB claims/checkpoints determine
what is eligible. Stage counters describe scheduling, not correctness or victory.

Without model configuration, the harness collects/queues only. Live inference
requires explicit permission and a matching model/endpoint cost declaration
before any stage claim. Tests inject MockTransport. No provider key is loaded by
default CLI invocation or inference-off harness mode. The gateway retains its
bounded output/schema validation, no redirect and no proxy settings.

The scheduler currently reaches REPRODUCING. Baseline sandbox dispatch, patch
execution, signed clean replay, seven-gate verification and human-approved PR
publication still require their admitted integrations. A reproduction proposal or
PROCESSED count cannot become VERIFIED. No authorization/authentication policy
or remote-code-execution gate was changed.

## CLI

Existing collection mode remains available. Explicit combined scheduling:

```powershell
# No I/O; returns NOT_RUN, exit 2.
uv run python -m scripts.run_worker --harness

# Scoped GitHub reads/local DB work; queues triage without inference.
uv run python -m scripts.run_worker --run --harness --max-ticks 6

# Explicit operator authorization for budgeted live inference.
uv run python -m scripts.run_worker --run --harness --max-ticks 6 --allow-live-inference --estimated-call-microusd 100000
```

The last number is a declared internal estimate (100000 microusd = $0.10), not
verified provider pricing, a purchase or an external billing cap. It must fit the
existing per-run budget. These commands were not used to make live calls during
implementation. Configuration is the existing WorkerConfig environment plus
NEBIUS_API_KEY/model/base URL only when live inference is explicitly selected.
Runtime DB role checks and loopback address pinning remain unchanged. Do not put
secrets into command arguments. SIGINT/SIGTERM stop the loop cooperatively;
shutdown does not cancel/delete remote resources or renew expired leases.

## Verification

Unit tests cover bounds, shutdown, stage fairness, idle waits, inference-off,
explicit live permission, declaration requirements and CLI signal restoration.
Real PostgreSQL tests use a tiny inert source fixture and mocked model response:
collection-to-triage succeeds, restart is read-only, and failed/uncertain inference
never creates a second request. They do not execute repository code on the host
or assert live provider behavior.

```powershell
uv run pytest tests/unit/test_agent_harness.py tests/unit/test_worker_startup.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For the full isolated PostgreSQL suite, use scripts.test_postgres with the same
420-second outer harness override documented in docs/memory.md. This does not
change any individual test, model, job lease or product execution timeout.


## 2026-10-22 — Source-informed bounded collection/triage harness

Base d5cf18e. Reviewed official Anthropic long-running-agent guidance and its
reference README, plus LangChain's harness anatomy and harness-engineering posts.
Sources and decisions are linked in docs/agent-harness.md. Implemented an original
fixed-stage scheduler using existing workers, typed state, memory, leases,
request reservations and journals; no framework/model/provider was replaced.

The harness alternates collection/triage fairly, queues handoff atomically, caps
steps/waits and stops cooperatively. Exceptions propagate without blind retry.
The existing CLI adds --harness and explicitly gated live inference with a
required declared per-call cost estimate. Default invocation performs no I/O;
inference-off never reads the model key. Signal handlers restore on exit/error.
The currently integrated path reaches REPRODUCING, not a repair verdict.

Observed checks, final exit 0:
- `uv run pytest tests/unit/test_agent_harness.py tests/unit/test_worker_startup.py -q --junitxml=docs/evidence/m3-harness-unit.xml`: **25 passed**.
- Focused isolated PostgreSQL harness selection: **13 passed**, 1060 deselected,
  zero skips, 10.13s (`m3-harness-focused.xml`). This preceded the additional
  positive CLI configuration parameter; the full run includes that case.
- Full disposable-PostgreSQL regression: **1074 passed**, zero failures/errors/
  skips, **333.06s**, `m3-harness-postgres.xml`. One existing upstream
  Starlette/AnyIO warning. Task-owned local DB/roles cleaned by the harness.
- Global Ruff lint/format passed (194 files); strict mypy passed (97 source
  files). Scoped credential-value scan and Git diff checks pass.
- `uv run python -m scripts.check_checkout`: M2_FIXTURE_ACCEPTED.

Initial TDD run failed on the absent harness module (8 cases, exit 1).
The first integration collection exposed duplicate test basenames; the new
integration module was renamed without changing pytest mode or weakening tests.
Formatting/import findings were corrected. The final source has no suppressed
checks. The CLI description was corrected to describe opt-in inference after
starting the full run; that edit changes help text only.

The tiny integration fixture uses synthetic source/model HTTP, real PostgreSQL
collection-to-triage and replay/restart assertions. Failure mode confirms exactly
one outbound mock request across uncertain recovery. No inference/sandbox/Tavily
call or remote repository write, merge or deployment occurred. Public docs and
an official reference README were read online without provider credentials.
No dependency/UI changes required repeating prior audits/browser checks.

M3 remains NOT CLOSED. The remaining critical work is admitted baseline/candidate/
clean execution, trusted report production and live seven-gate acceptance, then
approval/publication and submission packaging. This closes the combined
collection/triage scheduling increment, not the whole runtime graph.


## Authenticated triage continuation - 2026-10-24

Implementation4917e5c connects queued collection to the existing durable triage
controller without worker GitHub/vault credentials. Triage requires explicit
inference opt-in and a declared cost; default collection remains inference-off.
Current membership, lease/version/consent and sticky request journals remain
mandatory. See docs/authenticated-triage.md. Local fake-provider acceptance is
not live inference, baseline execution, VERIFIED or M3/M4 completion.


## Admitted baseline continuation - 2026-10-25

Implementations e78ea60/a061648 connect reproduction retention to a stage-specific
baseline queue and one-shot prepared-workspace dispatch. Collection workers do
not claim baseline jobs. New migration0028 preserves immutable submission claims;
nonempty-ledger rollback remains refused. See docs/baseline-worker.md for actual
REST/SDK findings, prerequisites and limitations. Tests use synthetic assessors
and fake HTTP; no live VERIFIED or M3/M4 completion follows from submission.
