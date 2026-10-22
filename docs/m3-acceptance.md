# M3 acceptance — 21 October 2026

The local workbench, reconnect path and on-demand research integration are
implemented and tested. **M3 as a whole is not closed**: its required live,
authenticated seven-gate clean replay has not been demonstrated. Local tests,
public documentation and a provider image UUID do not supply that proof.

## Observed implementation evidence

- Backend snapshot 65bccad: m3-workbench-research-postgres.xml records 905 tests,
  zero failures/errors/skips, 191.980 seconds of suite time. The focused report
  records 16 passing configuration, discovery and research-broker cases.
- The browser harness creates a fresh database and separate runtime role, starts
  the actual local API and production Next server, creates/cancels/reloads a small
  incident, and cleans up its database, roles and server processes. No repository
  source code executes. Status and missing gates come from actual persisted state.
- Synthetic UI contracts cover disconnected SSE replay with an unchanged cursor,
  missing gates, a rejected candidate, inert diff text and hash-checked log preview.
  These tests are labeled synthetic; their PASS observations are not live repairs.
- The retained live Tavily archive is rendered in the timeline in a separate UI
  contract. Its archived source content is real; surrounding UI responses are
  replay fixtures, not a live database or a new provider request.
- Ruff, strict mypy, UI lint/typecheck/build, npm audit and scoped credential scans
  are recorded in the implementation log. The UI is loopback development only;
  it is not public authentication or a deployed service. Retention is read expiry,
  not physical erasure, backup deletion or verified encryption deployment.

## Authorized live documentation lookup

The user explicitly approved one Tavily search. On 2026-10-21 the actual broker
submitted the closed Python 3.12 transaction-control query with at most two
results. No incident text, repository code, model request or sandbox execution
was sent. The provider returned SUCCESS / REMOTE_SERVICE, two sources and one
usage credit. The internal 50,000-microusd reservation is a declared estimate;
it is not confirmed USD billing.

The canonical retained record is docs/evidence/m3-live-research.json, SHA256
0272b42fec7ac4d270e256ae0fc6d5c56f2e5efd67ea7563e7f3766594202d0a.
The record was read back from the private database before that disposable DB
was removed. Sources preserve URLs, titles, retrieval times and advisory trust.
Ranking returned an Indonesian Python 3.12 contents page and the current Python
3.15 SQLite reference. This does not prove version-specific correctness or a
diagnosis. The search was not repeated. Subsequent tests reuse the archived bytes.

## External acceptance boundary

The existing real fixed-checkout baseline, candidate and independent replay
observations remain intact. They are unsigned fixed-fixture observations and
explicitly identify their image by provider UUID rather than verified OCI SHA256.
They cannot be relabeled as production VERIFIED or used as forged gate evidence.

Remaining acceptance requires a configured deterministic producer outside guest
and model control, assessed runner/image/configuration identities, and authentic
reports for all mandatory gates: baseline, regression, existing suite, clean replay,
scope, security and integrity. No default production producer/key is installed.
Physical retention and deployment storage controls also remain open. Deployment
or additional paid runner acceptance requires its own concrete authorization.

Provider documentation confirms VM-level workload isolation and per-spawn network
disable support, while directing customers to confirm required retention/deletion
and other deployment guarantees. This is a missing evidence/integration boundary,
not a claim that the provider is insecure:
[security and data handling](https://docs.tokenfactory.nebius.com/sandboxes/operate/security-and-data-handling),
[limits and retention](https://docs.tokenfactory.nebius.com/sandboxes/operate/limits-retention-and-usage).


## 22 October follow-up — current implementation alignment

Report export (`f1a25d5`), scoped memory (`d5cf18e`) and the bounded combined
collection/triage harness (`715831d`) are implemented locally. Latest full backend
report: docs/evidence/m3-harness-postgres.xml, 1074 passed, zero skips, 333.06s,
exit 0. These updates preserve the fixed fixture acceptance and existing UI.
They use synthetic provider/gate evidence in tests, not a new live repair.

The active runtime currently reaches REPRODUCING. Completing M3 requires admitted
execution/report collection, a trusted receipt producer with actual image/config
assessment, fresh correlated reports for all seven mandatory gates, and the real
outcome visible in the UI. Whole-filesystem saved-image retention and deployment
storage guarantees remain open. Do not mark M3 complete from harness or memory
unit tests. See todo.md's current focus; acceptance criteria are not relaxed.


## 22 October durable collection acceptance

Implementation c949e1e adds known candidate/clean operation observation collection,
private terminal records and audit/fencing/restart checks. Full local backend
report: m3-replay-collection-postgres.xml, 1104 passed, zero failures/errors/skips,
373.37s console, exit 0. Provider HTTP is synthetic in these tests. No live
sandbox or inference ran. This closes read-only collection/retention locally;
it does not establish trusted workspace measurements, receipt signatures or
live seven-gate acceptance. M3 remains open. See replay-collection.md and the
current-focus block in todo.md.
