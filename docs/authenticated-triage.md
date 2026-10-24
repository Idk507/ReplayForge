# Authenticated collection-to-triage worker

The authenticated worker now has an explicit triage mode. It claims only queued
TRIAGING jobs, asks the auth service to recheck current user/installation access
and repository consent, then calls the existing `run_triage` controller with
retained context. GitHub user tokens and vault keys remain inside the auth service.
This is a reproduction proposal handoff, not baseline execution or a repair.

## Boundaries

POST `/internal/v1/collection/authorize-triage` uses the existing independent
workload key, rejects browser Origin/fetch headers, and checks the exact lease,
run version, stage, cancellation and current repository access. Its response
contains only run ID, version and ADMITTED status. It is a transient access check,
not a reusable approval, model tool or permission to execute repository code.

The worker correlates the response before calling the existing request budget
and inference journal. The journal checks current lease/version/consent again,
reserves the exact outbound request and retains the validated proposal before
queueing the baseline job atomically in authenticated mode. Legacy triage keeps
its prior finish-job behavior. Unknown inference remains sticky across restart. No automatic
retry is added. No patch, baseline, sandbox, verifier or publisher is dispatched.
Auth/budget errors stop the bounded worker with a sanitized reason. The separate
admitted baseline worker is documented in `baseline-worker.md`; queueing is not
permission to execute and default startup still dispatches no sandbox work.

## Explicit operator startup

Restart the existing auth API after updating the code. Keep it loopback-only,
without access logs. Its existing collection broker opt-in must be enabled.
Use a dedicated ignored `.env.triage` containing the four workload entries from
`authenticated-collection.md` plus NEBIUS_API_KEY, NEBIUS_BASE_URL and
NEBIUS_MODEL_PRIMARY. Use an inference-only provider key. Never include auth DB,
GitHub, Tavily or vault credentials. Configure the exact approved model/endpoint.
The launcher drops unrelated inherited environment values and does not obtain
credentials from the parent process.

The role needs the existing triage/reproduction and advisory memory/research
journal grants, in addition to the collection worker's run/job/event grants.
Provision these from the existing migrations with restricted operator authority;
do not grant auth tables, repository grant INSERT or administrative privileges.
The previously provisioned collection-only role is insufficient for triage.
No production grants or paid calls were applied during this implementation.

Only after authorizing the specific provider usage:

```powershell
uv run --no-env-file python -m scripts.launch_authenticated_worker --run --env-file .env.triage --triage --allow-live-inference --estimated-call-microusd 100000 --max-ticks 1
```

100000 microusd is an operator-declared $0.10 request estimate, not verified
provider pricing or an external billing cap. It must fit the existing run budget.
All three triage/inference/estimate flags are required before configuration or
database I/O. The default collection mode still rejects provider keys. Without
`--run`, neither mode reads configuration or performs I/O. The existing launcher
300-second outer limit and worker operation/lease limits remain unchanged;
restart cannot extend a lease or erase an uncertain inference intent.

## Acceptance and remaining dependency

Tests use one small inert source file, synthetic GitHub identity/access and a
fake HTTP model response with real disposable PostgreSQL. They prove queue claim,
current access check, token-free handoff, reproduction retention and no second
request on a completed job. Revocation blocks inference. Unit tests reject
partial opt-in flags and mismatched admission run/version. Existing inference
uncertainty tests remain part of the full regression.

This does not prove a live model call or close M3. The next boundary is admitted
baseline/materialization and independently established source/image/configuration
and collector trust. Existing signed-baseline reducers reject synthetic or missing
production bindings. Do not synthesize those facts from expected hashes or an
image UUID. Then connect patch/replay and the mandatory gates. Human approval,
GitHub write-token brokerage, live PR and hidden evaluation remain separate M4 work.
