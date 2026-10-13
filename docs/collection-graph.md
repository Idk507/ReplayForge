# Local collection graph and recovery

run_collection coordinates a bounded LangGraph plan/reducer loop over the
persisted RunState. CREATED -> VALIDATING -> QUEUED -> COLLECTING -> TRIAGING
is the successful path. Scope consent and exact source commit/path rules are
checked against persisted grants before progression and context collection.
POLICY_BLOCKED records scope/source denial; INCONCLUSIVE records collector
rejection. Terminal cancellation is returned unchanged. No stage beyond TRIAGING
is dispatched by this graph, and it has no model, sandbox or PR tools.

Planning returns validated CollectionDelta values. Only the reducer commits via
the lease-fenced Store. Context artifact/reference/event and TRIAGING checkpoint
are atomic. A new invocation loads RunState from PostgreSQL, recreates the graph
and continues at that stage. Completed invocation replay has no new writes.
Unexpected worker/setup exceptions propagate; they are not retried as successful
collection. A replacement worker must acquire a new lease after expiry; old
tokens cannot commit. Callers supply the same trusted snapshot/input on recovery.

This uses application domain checkpoints, not LangGraph-native saver tables.
Pure uncommitted planning is recomputed after interruption. That is sufficient
for this collection-only slice; future model/network/execution nodes need durable
action intents, external IDs and reconciliation before retry. Exactly-once
external effects are not established. A background polling worker, complete agent
graph, scheduling/backoff and automatic exhausted-job routing remain pending.

Raw snapshot/error/secrets stay in private closure data, outside graph state.
The ephemeral delta can contain sanitized context but is not persisted by
LangGraph or exposed through a stream endpoint. Hosted tracing is disabled with
tracing_context(enabled=False), even when LANGSMITH_TRACING is set; callbacks
are empty. Tests independently check that the collector observes tracing off.
This is not a substitute for a process egress policy in deployed workers.

Tests use synthetic preloaded snapshots and real disposable PostgreSQL. They
prove collection, artifact recovery, no duplicate writes after completed replay,
interruption before context commit, replacement-lease resume, old-worker fencing,
explicit failure routing and cancellation. They do not prove authenticated fetch,
complete incident ingestion, real inference or sandbox repair execution.

Reference: https://docs.langchain.com/oss/python/langgraph/graph-api .
