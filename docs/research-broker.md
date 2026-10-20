# On-demand investigation research

Migration 0018 adds sticky query intents and append-only expiring research reports.
ResearchBroker.investigate is called only by trusted orchestration with a live
worker fence, authenticated owner, exact checkpoint version, current repository
consent and TRIAGING/REPRODUCING/PATCHING stage. It has no model-call, execution
or publication tool. DocumentationQuery remains the curated package/topic schema;
incident text, source code and caller-selected destinations cannot enter it.

Explicit allow_live=True and a positive operator-declared USD estimate are required.
Before submission, one search, 25 seconds of requested execution allowance and the
declared estimate are debited atomically with the sticky intent/version/event.
The time/estimate are reservations, not actual latency enforcement or confirmed
billing. Provider credit usage is retained if supplied; unknown USD cost stays
unknown. Budget exhaustion closes INCONCLUSIVE using the existing budget reducer.

No automatic retry follows a transport failure, uncertain submission, worker loss
or report-retention failure. The same query cannot submit again after a recorded
intent, including after successful completion. Owner-scoped report reads reconcile
completed output; a missing report means uncertainty, not permission to retry.

Canonical report, source/run binding, SHA256 reference in RunState.research_refs,
version and RESEARCH_RETAINED event commit together. GET /api/v1/runs/{id}/research
requires existing local bearer identity and returns bounded, hash-checked private
records within 24-hour read expiry. Runtime UPDATE/DELETE are not granted. Physical
purge, deployment encryption and backup policies remain separate operations work.

run_triage loads these retained reports under explicitly named
untrusted_documentation fields in its already-delimited untrusted context. The
existing total context byte/count limits still apply; expired/missing research
blocks this path explicitly. UI timeline citations preserve title, URL, retrieval
timestamp and REMOTE_SERVICE/TEST_TRANSPORT provenance. Titles/snippets remain
escaped advisory text; links use only curated HTTPS documentation hosts.

Offline acceptance includes real PostgreSQL budgets, concurrent intent exclusion,
ownership/lease/version/cancellation/exhaustion denials, poisoned snippet retention,
and a synthetic model transport that receives citations solely as untrusted data.
This is not live Tavily or Nemotron evidence. One prepared fixed-query live check
exists in scripts.accept_research and requires separate charge authorization and
the disposable test environment. No live lookup has been performed by default.
