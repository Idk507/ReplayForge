# ADR 013: Use domain checkpoints for collection graph recovery

For the collection-only LangGraph slice, PostgreSQL RunState and its atomic
events/artifacts remain the sole durable authority. Planning nodes return typed
deltas; one reducer performs fenced transactions. Recreate the finite graph
from persisted stage on restart rather than persisting a second native graph
checkpoint containing uncommitted sanitized context or raw closure inputs.

This avoids divergent checkpoint authority and preserves the rule that domain
checkpoints contain content IDs rather than raw data. Planning can be safely
recomputed because it only reads scope and constructs bounded sanitized context.
Production graph persistence/action ledgers must be revisited before introducing
non-deterministic model calls or external side effects. Native LangGraph pending
task recovery and exactly-once remote effects are not implemented by this choice.

Explicitly disable hosted tracing for all graph execution. Schema-private data
is not inherently private to streaming/tracing systems; no graph stream API or
external callback is exposed. Existing persisted safe run events remain the
operator-visible audit path.
