# ADR 010: Compile public documentation queries before optional search

Status: accepted for the local M0 adapter; live and agent integration pending.

Research can leak incident data through queries and introduce hostile instructions
through results. Accept only a closed package/topic contract and numeric version,
compile queries deterministically, and restrict sources to registered official
documentation hosts. Do not expose free-form search or source-fetch tools.

Require feature enablement plus explicit live admission. Bound attempts and
responses, return sanitized structured observations, and label all result text
untrusted and unable to authorize execution. Keep unknown usage unknown.

This restricts research breadth deliberately. The adapter cannot independently
enforce durable run budgets or establish source truth. Those responsibilities
remain with PolicyEngine, BudgetController and deterministic verification before
M3 integration. Offline tests do not satisfy M0 live-access acceptance.
