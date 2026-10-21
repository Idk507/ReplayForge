# Human approval policy — M4 started

`services.approval.policy.validate_decision` is a deterministic validation kernel
for the future durable approval gate. It compares every immutable binding:
run, repository, owner, source commit, admitted diff SHA256, authenticated verdict
reference, target branch and policy version. The current authenticated reviewer
grant must match the decision's reviewer and repository/owner. Rejected decisions,
consumed nonces, naive clocks, future issuance, expiry and TTLs over 15 minutes
are denied. A narrow branch vocabulary rejects traversal/ref-expression forms.

The expected binding must come from an actual retained VERIFIED checkpoint and
verdict. The permission must come from independently authenticated human identity
and current repository authorization. Neither can be supplied by model/browser
JSON as proof. The diff reference identifies actual admitted diff bytes, not an
LLM statement that its patch was verified.

This kernel does not authenticate users, persist decisions, issue signed tokens,
consume nonces, advance stages or contact GitHub. A HumanDecision object is
untrusted unless its durable provenance is authenticated. Its
can_authorize_publication field is always false. A future journal/publisher must
reload identity/permission/current binding and validate/consume the durable nonce
atomically with a durable publication intent. Idempotent provider recovery must
discover an existing PR; it must never consume an approval and blindly resubmit.

Local development authentication remains unchanged. M4 approval and PR tasks
remain incomplete until durable storage, authenticated human endpoints, nonce
consumption and publication recovery pass acceptance. No PR was created.

## Observed validation — 2026-10-21

`uv run pytest tests/unit/test_approval_policy.py -q
--junitxml=docs/evidence/m4-approval-policy.xml` exited 0: **20 passed**, no
failures/errors/skips. Cases cover exact matching, altered patch/source/target/
verdict, wrong reviewer/repository/owner, expiry, future issuance, excessive TTL,
consumed nonce, rejection, unsafe branch forms, naive clocks and non-boolean
consumption state. These cases are also in the final 973-test disposable
PostgreSQL regression. Ruff and strict mypy passed. Tests do not establish
real human identity, durable one-use enforcement or live GitHub acceptance.
