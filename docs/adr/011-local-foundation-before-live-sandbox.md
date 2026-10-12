# ADR 011: Continue local foundation while sandbox activation is pending

The user explicitly authorized local foundation development while sandbox
activation is pending. This supersedes the earlier interpretation that M0
prevented all M1 work. M0 live acceptance remains open; no operational inference,
isolation or verified-repair claim follows from local tests.

RunState stores immutable identity, bounded artifact references and action
counters. The pure transition reducer validates restored checkpoints, expected
versions and legal progression. Cancellation is terminal. It refuses VERIFIED
and PR publication transitions until dedicated trusted services exist.
Baseline/test and patch references are required for progression, but their
presence is not authentication or proof of test outcomes.

Action budget consumption fails closed, does not refund failed calls, and
returns a new validated checkpoint. Spend/time budgets, durable atomic storage,
authorization and dispatch integration remain unimplemented. Concurrent workers
must commit counters and stage changes in one database compare-and-swap before
external dispatch. Model-produced checkpoints must never be trusted writes.

The next slice is PostgreSQL migrations and transactional checkpoint/job tests.
