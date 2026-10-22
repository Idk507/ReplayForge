# ReplayForge — Milestone Specifications, Dependencies and Acceptance Tests

| Milestone | Dates (2026) | Demonstrable artifact | Exit test | Criticality |
|---|---|---|---|---|
| M0 Platform | Oct 12–13 | Token Factory proof + remote isolated runner | Real NVIDIA inference and runner smoke | Stop/go |
| M1 Foundation | Oct 14–16 | Run API + event ledger + graph checkpoint | Create/cancel/restart/SSE integration | Must |
| M2 Repair | Oct 17–20 | Baseline reproduction and candidate execution | Real checkout fixture fail then patched pass | Must |
| M3 Trust + UI | Oct 21–23 | Manifest, clean replay, dashboard, Tavily | Verified gate from independent runner and visible UI | Must |
| M4 PR + Evaluation | Oct 24–26 | Reviewer approval, PR, benchmark report | Approval digest checked + one PR, hidden oracle result | Must |
| M5 Submission | Oct 27–29 | Hosted demo + open repo + video + Devpost | All public links and official fields validated | Must |

## M0 acceptance
A live response from a NVIDIA Nemotron model on Nebius Token Factory including request timestamp and configured model is required. A real remote isolated runner must execute a harmless command while preventing forbidden access and must destroy itself. If remote runner is not ready, document fixture-only evaluation boundary immediately. Setup and credits can vary by account; never assume granted access.

## M1 acceptance
POST /runs produces durable record, one event and a job claim; unauthorized calls fail closed. Worker crash before next checkpoint can recover without duplicate run. SSE reconnect with Last-Event-ID returns only unseen events. Checkpoint includes exact source SHA, max attempts and budget. Redaction tests confirm fixture secrets absent from model payload.

## M2 acceptance
Checkout fixture baseline must show duplicate transaction condition with a test assertion that fails for the intended cause. Candidate patch from real inference applies only allowed paths. Runner records original SHA and current tree hash. Patched tests pass in separate execution; violating path/command policy blocks execution and generates audit record. Reproduction syntax error results INCONCLUSIVE rather than a false success.

## M3 acceptance
A second clean runner checks the exact patch and test artifacts and passes mandatory gates. Tampering with one SHA or omitting one test must prevent VERIFIED. UI shows actual status and allows the user to inspect logs. Tavily call carries source URL, title and timestamp; poisoning external snippet with malicious instructions does not trigger a forbidden tool.

## M4 acceptance
Human reviewer must approve source commit + patch digest + target branch; mutation after approval invalidates it. GitHub write uses isolated credentials and idempotency; replaying same request creates only one PR. Run 3 named fixtures if stable and a meaningful hidden variation sample; publish per-case denominators, rate and error categories. One intentionally rejected candidate should be included in demo preparation.

## M5 acceptance
Public demo operates without exposing privileged controls to anonymous users; project repo includes LICENSE and README and code needed to reproduce test fixtures. Video is public and under 3 minutes. Devpost specifies Coding and Agentic Engineering, actual NVIDIA+Nebius technology, optional Tavily usage, feedback and all required links. Submit on Oct 29 and keep endpoint available during Dec 1–15 judging window.

## Milestone change control
For a slipped milestone: identify failing exit test, block dependent scope, select minimum safe fix and cut optional features first. Never claim pass on an incomplete milestone. Weekly reporting is not enough during 18-day sprint; update `todo.md` and `docs/implementation-log.md` after each completed acceptance gate. Keep the final 48 hours free from large architectural changes.


## Implementation alignment snapshot — 2026-10-22

The acceptance criteria above remain unchanged. M2's fixed-checkout contingency
exit is mapped in docs/evidence/m2-final-exit.json; broader general-repository
execution is not complete. M3 remains open for authentic seven-gate clean replay.
Local memory and the combined collection/triage harness are accepted increments,
with 1074 passing backend regression cases retained at implementation 715831d;
they do not close M3. M4's pure approval kernel does not close durable approval,
PR publication or hidden evaluation. M5's deployment/submission exit is open.

Use todo.md's current-focus block for the next task and implementation-log.md for
actual test commands/results. Complete the checkout journey before optional
integrations. The teammate owns authentication; required human write approval
and public access boundaries still apply. Submit packaging remains mandatory.
