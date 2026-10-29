# ReplayForge demo runbook

## Scope and truthful labels

Show one checkout duplicate-charge incident. Prioritize the source commit,
observed failing baseline, constrained patch and separately passing replay.
The retained M2 checkout observations are historical remote fixture evidence,
not a live general-repository repair or production VERIFIED verdict.
Live GitHub onboarding and small-file collection are separately evidenced.
Do not splice these into a claim that one fully integrated live run succeeded.
The current synthetic four-phase PostgreSQL acceptance is a separate local test
run; its signed result must be identified as test-only evidence. The local
`/evaluation` workbench page is an unauthenticated inventory snapshot, not a
benchmark attestation.

## Offline rehearsal (no cloud charges)

```powershell
uv sync --locked
uv run python -m scripts.check_checkout
uv run ruff check
uv run ruff format --check
uv run mypy
```

The checker must return M2_FIXTURE_ACCEPTED with no failed checks, and both
authorization booleans false. Inspect the exact report, triage and patch in
`docs/evidence/m2-checkout-e2e/`; preserve original timestamps and identities.
An offline rehearsal must not invoke `scripts.e2e_checkout --live`.

## Local interactive flow

Use the existing GitHub loopback startup in `github-authentication.md` and
collection-only startup in `authenticated-collection.md`. Never expose operator
credentials in the recording. Connect an authorized repository and pin its SHA.
Describe a small incident with a relative reference such as `src/checkout.py:12`.
Only registered, consented paths may be selected. Absolute paths, URLs and
traversal cannot expand scope. Missing files or oversized context are explicit
limitations, not successful investigation.

The collection worker queues TRIAGING. It does not start inference or sandbox
execution. Show durable status/events and the source SHA, without claiming a
repair has run. Browser smoke tests use synthetic identities and remain distinct
from the live read-only source acceptance artifact.

## Thirty-second opening

1. State the problem: retrying checkout can charge twice.
2. Show the captured baseline assertion and its original source identity.
3. Show the minimal checkout patch and captured candidate/clean replay results.
4. State clearly which evidence is retained and which interaction is live.

For the evaluation presentation, open `http://127.0.0.1:3000/evaluation` only
while the disposable API/browser harness is running or after starting the web
app using `apps/web/README.md`. The local acceptance harness exits after its
browser run, so the captured report and screenshot are the reproducible record.

Follow with the real onboarding/collection interaction, evidence identities and
limitations. Show NVIDIA model identity and Tavily citations only from actual
retained calls; do not make fresh calls merely for a recording. Never claim a
draft PR exists from an internal publication intent.

## Release blockers still open

- A single integrated live incident-to-trusted-seven-gate workflow remains open;
  local synthetic worker acceptance does not establish it.
- Production signer/collector/assessor trust, qualified execution runtime and
  authentic checkout receipts remain open.
- Exact-review UI and publication control have local browser tests; GitHub writes
  are mocked, no PR was created, and the checkout POC excludes a real PR.
- Fixture execution and hidden evaluation remain open; current inventory has
  zero attempts and unavailable ETL/concurrency/holdout cases.
- Public deployment, anonymous-access review, final video and submission links.

These remain M3/M4/M5 acceptance requirements. This runbook is a rehearsal guide,
not proof of completion. No deployment, publication or paid call is authorized
by running its offline commands.
