# Durable triage handoff

Collection remains offline by default. An operator may explicitly enable
`--queue-triage` on `scripts.run_worker --run`, or pass `queue_triage=True` to
the trusted collection worker/graph. This does not make an inference request.
The context, source manifest, TRIAGING checkpoint and queued job commit in the
same transaction. The old lease token is cleared and the next stage gets a
fresh three-claim recovery allowance. There is no post-commit enqueue gap.

`services.orchestrator.worker.tick_triage` claims one TRIAGING job with a new
lease. The operator supplies NebiusConfig and an explicit ModelCostPolicy;
the existing triage controller loads owner-scoped retained context, reserves the
exact request and estimated budget once, submits once and retains the validated
test. Successful retention commits REPRODUCING and closes the triage job in
one transaction, avoiding a checkpoint/job-completion crash gap. No generated
test is executed on the worker or API host.

The default collection claim excludes TRIAGING; the triage claim includes only
TRIAGING. Thus two worker roles cannot accidentally process each other's job.
An uncertain inference intent survives worker death. A new claim after lease
expiry encounters TRIAGE_SUBMISSION_UNCERTAIN before provider traffic; there
is no blind retry or second budget debit. Three exhausted claims use the
existing INCONCLUSIVE/LEASE_EXHAUSTED path. Errors propagate as bounded codes.
The trusted caller controls scheduling and must not wrap errors in an unbounded
retry loop. A 30-second lease remains a fence, not a promise that any external
request completes before expiry; late output fails closed during retention.

Programmatic worker entry point:

```python
from uuid import uuid4
from packages.contracts.inference import ModelCostPolicy
from packages.providers.nebius import NebiusConfig
from services.orchestrator.worker import tick_triage

config = NebiusConfig.from_env()
# Supply your own approved estimate; this is not a provider price quote.
policy = ModelCostPolicy(
    model=config.model,
    base_url=config.base_url,
    policy_version="operator-estimate-v1",
    estimated_spend_microusd=100_000,
)
# store is an explicitly configured Store using the restricted runtime DB role.
result = tick_triage(store, uuid4(), config, policy)
```

Calling this entry point authorizes an actual model request when a queued job
exists. There is no import-time execution or automatic environment-file loading.
Do not run it as a routine test. Contract tests use synthetic HTTP and a small
source fixture, with the real disposable PostgreSQL harness.

This closes the collection-to-triage worker boundary only. REPRODUCING is a
retained proposal, not observed baseline failure. Admission-gated remote
baseline/candidate submission, independently signed reports, verifier gates and
approval/PR publication remain separate work. The default CLI does not claim
to execute the full repair graph or run this inference worker automatically.
