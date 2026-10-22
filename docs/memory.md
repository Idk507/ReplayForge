# Run recovery and reusable repair memory

## Implemented boundary

Per-run recovery uses the existing PostgreSQL RunState checkpoint, fenced job
leases, append-only intent/result journals and transactional event ledger. Workers
resume retained stages; uncertain inference/submission stays sticky rather than
creating another billable request. This feature preserves those implementations.

Cross-run memory is a read-through view of durable accepted verification records.
`RunMemory(store).recall(run_id, owner)` reads at most three prior records in
stable retention order. It returns bounded RepairLesson objects containing the
source run/commit, patch/manifest/verification hashes, accepted outcome, failed
and missing gates, timestamps and an explicit reproduce-and-verify-again rule.
No causal diagnosis, code, incident text, arbitrary lesson text, raw output,
credential, prompt or hidden reasoning is copied into a new memory database.

Memory is not a new authority. Only records previously retained by the trusted
VerificationJournal qualify; a parsed card cannot authorize execution,
verification, approval or publication. Authentication of the original evidence
happens at the existing acceptance boundary. Recall checks the retained record's
canonical bytes, digest and checkpoint bindings; it does not retroactively renew
the original short-lived signature or re-run its gate evaluation.

## Scope and retention

Current owner/repository consent must permit the current commit. Historical
records must share the owner, repository and policy version, and their commits
must still be in the current grant. The current run never recalls itself.
Cancellation, wrong owner, invalid consent and corrupted selected records fail
closed. Expired verification records or evidence bundles are excluded. Returned
expiry is the earlier of the two source expiries. No tenant-wide/global learning
or vector index is enabled. No new database role permissions are required.

Memory survives process restart because the underlying records are durable.
It deliberately does not outlive evidence read retention (currently 24 hours by
default). Expiry is not physical erasure. Longer-lived lessons require an explicit
evidence-retention design; they are not silently retained forever.

The current implementation recalls accepted VERIFIED/NOT_VERIFIED/INCONCLUSIVE
stages. Future approval/publication stage integration must preserve the historical
verification identity when exposing those runs through memory. Metadata recall
is not semantic incident matching, causal root-cause learning or a claim of
improved repair accuracy. No live repair history is fabricated to populate it.

## Triage integration

The existing `run_triage` path recalls automatically before request reservation.
`with_advisory_memory` fills only unused space in the existing 8-entry/4096-byte
context limit, newest entries first. If no card fits, it leaves current incident,
source and research evidence intact. Empty history adds no context entry.
Triage's system prompt labels historical outcomes as potentially irrelevant,
untrusted advisory evidence and requires current reproduction. The exact outbound
request, including any selected cards, is covered by the existing request SHA256
and budget reservation. Recall causes no additional model/provider request.

The returned tool-shaped metadata includes current run trace, observation time,
policy result, artifact refs and explicit false execution/verification authority.
Recall does not change checkpoints, events, budgets, jobs or TTLs. Completed
triage still reconciles before recall and makes no additional inference request.
No memory endpoint, UI, free-form memory writer or authentication change ships
in this increment. The subsequent bounded scheduler is documented in docs/agent-harness.md.

## Reproducible checks

Small synthetic signed records and mocked inference only; no live inference,
sandbox, Tavily or GitHub request is needed. Use the isolated local database
harness for integration checks; it prompts for the administrator password and
cleans its random databases/roles.

```powershell
uv run pytest tests/unit/test_memory_context.py tests/unit/test_triage_gateway.py -q
uv run python -c "import sys,subprocess; from scripts import test_postgres as t; call=subprocess.run; subprocess.run=lambda args,**kw:call(args,**{**kw,'timeout':420}); sys.argv=['test_postgres','--evidence-name','m3-memory-postgres.xml']; sys.exit(t.main())"
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The full suite currently needs the documented outer harness timeout override
(420 seconds) used in docs/prepared-nebius-backend.md; individual tests and
product execution limits are unchanged.


## 2026-10-22 — Durable recovery and repository-scoped lesson recall

Base f1a25d5. Preserved existing PostgreSQL checkpoint, lease and sticky-intent
recovery. Added bounded read-through RunMemory over accepted verification records:
same owner/repository/policy, currently consented source commits, canonical/hash
and checkpoint binding, verification/evidence expiry and cancellation checks.
Cards retain source/patch/manifest/verdict identities and failed/missing gates;
there is no free-form memory writer, extra model call or new vector/database
service. Recall is advisory and cannot authorize actions or verification.

Connected recall to run_triage within existing 8-entry/4096-byte request bounds,
without displacing current evidence. The exact budgeted request hash includes
selected lesson bytes. Completed triage still reconciles without another request.
Moved the unchanged closed GateName vocabulary into contracts while preserving
its existing verifier import. Auth/UI and provider keys were unchanged.

Observed evidence (all final checks exit 0):
- Focused unit/gateway run: **14 passed**, zero skips.
- Real PostgreSQL + synthetic HTTP memory-to-triage/restart handoff:
  **1 passed**, 1056 deselected, m3-memory-handoff.xml.
- Final full disposable-PostgreSQL suite: **1057 passed**, zero failures/errors/
  skips, **303.33s**, m3-memory-postgres.xml. One existing upstream Starlette/AnyIO
  deprecation warning. The harness cleaned its task-owned DB/roles.
- `uv run ruff check .`, `uv run ruff format --check .`: pass (191 files).
- `uv run mypy`: pass (96 source files). Diff and scoped credential-value checks
  pass. `scripts.check_checkout`: M2_FIXTURE_ACCEPTED.

Initial missing-module RED and intermediate focused failure reports are retained
as m3-memory-red.xml.gz and m3-memory-focused.xml.gz. An overly strong grant row
lock initially required privileges unavailable to the runtime role; recall now
uses the existing read-only grant permission, without broadening grants. New
handoff-test setup was corrected to use triage_only and a source-manifest ref;
no production gate was weakened. The full suite covers the corrected test.

No paid inference, sandbox, Tavily call, remote repository write, merge or deploy
occurred. Gate signatures/provider responses in memory tests are synthetic; this
is not live memory accuracy or live VERIFIED acceptance. No dependency/UI changes
required repeating their earlier audits/browser checks. Details: docs/memory.md.

Limitations: structured outcome recall, not semantic/root-cause learning;
retention remains tied to source evidence (currently 24h default); future
approval/publication stage recall and a UI view remain unimplemented. M3 is not
closed by memory. Separate requested harness specifications are still missing.


Review lifecycle integration: accepted VERIFIED outcomes remain recallable after
approval/rejection/publication claim only when matching durable review/intent
provenance exists. The original verifier outcome and evidence expiry are
preserved; reviewer permission is never recalled as authority. Missing/corrupt
review provenance fails closed. See approval-journal.md and the implementation
log for the reproduced regression and final test evidence.
