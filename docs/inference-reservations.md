# Request-bound inference reservations

The production triage and patch controllers require an explicit
`ModelCostPolicy` before their first provider request. The policy binds the
configured model and base URL, a versioned operator declaration and a positive
estimated cost in USD micro-units. This is configuration, not cloud-use
authorization, verified provider pricing or a provider-side billing cap.

The gateway validates the endpoint/model, serializes its JSON body once, and
calls the controller's `before_submit` hook with a safe `RequestIdentity`.
The SHA256 covers the exact UTF-8 bytes subsequently sent with Content-Type
application/json. Identity also records the model, base URL, configured
30-second HTTP timeout and 1200 completion-token cap. Prompts, source, responses,
credential headers and reasoning are not stored in reservation metadata.

The existing sticky triage/patch intent transaction fences owner, live lease,
version, cancellation, stage and repository consent. It then debits one model
call, 30 configured timeout seconds and the declared cost together, writes the
request/cost metadata and emits BUDGET_RESERVED. The gateway submits only after
that transaction commits and the controller rechecks the version. Provider or
output-validation failure does not refund the estimate or permit blind retry.
Completed retention still reconciles without another request or policy charge.

If any of those three budgets is exhausted, the same transaction instead closes
INCONCLUSIVE with BUDGET_EXHAUSTED and completes the lease, without creating a
dispatch intent or partially debiting resources. A failed audit write rolls
back checkpoint and intent. Concurrent reservations cannot debit twice.

Migration 0013 adds bounded nullable dispatch_json columns to the existing
append-only intent tables; runtime SELECT/INSERT grants do not expand. Historical
count-only intents remain readable as legacy rows. They are not dispatch proof:
controller retry encounters the existing uncertain intent and cannot convert it
into a fresh paid call. Schema downgrade removes the new metadata; back up and
review retained billing/audit records before any production downgrade.

Internal usage, with an operator-reviewed estimate rather than an invented rate:

```python
policy = ModelCostPolicy(
    model=config.model,
    base_url=config.base_url,
    policy_version=operator_policy_version,
    estimated_spend_microusd=operator_cost_estimate,
)
result = run_triage(store, lease, owner, config, cost_policy=policy)
```

The low-level gateways retain their explicit fixture/probe usage; adding this
feature does not start the worker, enable arbitrary repositories or authorize
a provider call. The sandbox capability gate remains unchanged.

Limitations: HTTPX timeouts bound socket phases/inactivity, not an absolute
end-to-end wall-clock deadline. This reservation accounts for configured
timeout, not measured elapsed time. Absolute deadlines, elapsed-time/actual
usage reconciliation, live price validation, production runner admission and
full polling-worker integration remain separate requirements.

Triage response collection now enforces its existing byte limit incrementally,
stopping before consuming the rest of an oversized response. The regression
failed when the client buffered the entire response before checking its size.
