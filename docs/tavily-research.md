# Optional documentation research

The adapter is connected to the durable on-demand research broker and triage's
untrusted context, with advisory timeline citations. One user-approved live lookup
succeeded on 2026-10-21 with two sources and one reported usage credit. See
research-broker.md and m3-acceptance.md for provenance and remaining boundaries.
This does not complete M3's authenticated clean-replay gate.

`uv run python -m scripts.check_tavily` makes no network request and exits 2.
After explicit authorization for potential credit consumption, set
ENABLE_TAVILY=true and TAVILY_API_KEY securely in the process environment, then
run `uv run python -m scripts.check_tavily --live`. This submits one fixed
Python 3.12 documentation search. Exit 0 means allowed source results were
returned; it does not establish their correctness or verify a repair.

DocumentationQuery accepts only registered package names, numeric versions and
topic enums. It cannot carry incident text, repository content, caller domains
or arbitrary queries. The destination is fixed HTTPS api.tavily.com/search.
Results must use exact curated documentation hosts without credentials, query
strings or fragments. URLs are never fetched by this adapter. Redirects,
environment proxies, automatic retries and compressed responses are disabled.
Response bytes, individual fields, results and request timeouts are bounded.

Titles and snippets are normalized, conservatively redacted and truncated.
This is not a general DLP system. All returned text remains untrusted external
data, including instructions embedded in documentation. It grants no execution
authority. Canonical sanitized URL/title/snippet JSON is SHA256 hashed; embedded
content IDs are not proof of durable artifact storage. Retrieval timestamps are
local observations. Missing credit usage stays unknown; dollar cost is never
invented. Failures expose stable codes instead of raw provider responses.

Two attempted searches are admitted per client, including failed requests;
concurrent callers cannot exceed this limit. This process-local cap is not a
durable run budget. The integrated broker separately enforces run-level policy,
durable search/time/declared-spend reservation and private report reads. Tests use
HTTPX MockTransport and identify TEST_TRANSPORT; the retained live acceptance
record identifies REMOTE_SERVICE. Neither origin grants execution authority.

API reference consulted: [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search).
