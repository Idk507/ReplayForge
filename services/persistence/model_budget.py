"""Commit requested-work reservations or durable exhaustion before network I/O."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from packages.contracts.budget import Budget
from packages.contracts.execution import TestCommandPlan
from packages.contracts.inference import ModelDispatch
from packages.contracts.run import RunState, Stage
from packages.policies.budget import BudgetError, consume
from packages.policies.transitions import transition
from services.persistence.store import Store, StoreError


class _Exhausted(Exception):
    """Internal signal: commit terminal checkpoint, then return a safe denial."""


@contextmanager
def reservation_transaction(
    store: Store,
) -> Iterator[psycopg.Connection[dict[str, object]]]:
    exhausted = False
    with store._connection() as conn:
        try:
            yield conn
        except _Exhausted:
            exhausted = True
    if exhausted:
        raise StoreError("BUDGET_EXHAUSTED")


def debit_model(
    store: Store,
    conn: psycopg.Connection[dict[str, object]],
    run: RunState,
    dispatch: ModelDispatch | None,
) -> Budget:
    """Legacy count-only intents stay inert; dispatch requires explicit metadata."""
    if dispatch is None:
        return consume(run.remaining_budget, "model_calls")
    dispatch = ModelDispatch.model_validate(dispatch.model_dump())
    return _debit(
        store,
        conn,
        run,
        (
            ("model_calls", 1),
            ("execution_seconds", dispatch.request.timeout_seconds),
            ("estimated_spend_microusd", dispatch.cost.estimated_spend_microusd),
        ),
    )


def debit_baseline(
    store: Store,
    conn: psycopg.Connection[dict[str, object]],
    run: RunState,
    plan: TestCommandPlan,
) -> Budget:
    """Reserve the immutable plan's full requested duration with its command."""
    plan = TestCommandPlan.model_validate(plan.model_dump())
    return _debit(
        store,
        conn,
        run,
        (("sandbox_commands", 1), ("execution_seconds", plan.timeout_seconds)),
    )


def debit_research(
    store: Store,
    conn: psycopg.Connection[dict[str, object]],
    run: RunState,
    estimate_microusd: int,
) -> Budget:
    """Reserve one search, its bounded request time and operator estimate."""
    return _debit(
        store,
        conn,
        run,
        (
            ("research_searches", 1),
            ("execution_seconds", 25),
            ("estimated_spend_microusd", estimate_microusd),
        ),
    )


def debit_materialization(
    store: Store, conn: psycopg.Connection[dict[str, object]], run: RunState
) -> Budget:
    """Reserve the fixed15-second upload command before its immutable intent."""
    return _debit(
        store, conn, run, (("sandbox_commands", 1), ("execution_seconds", 15))
    )


def debit_replays(
    store: Store,
    conn: psycopg.Connection[dict[str, object]],
    run: RunState,
    candidate: TestCommandPlan,
    clean: TestCommandPlan,
    suite: TestCommandPlan | None = None,
) -> Budget:
    """Reserve replay actions atomically; exhaustion never partially debits."""
    candidate = TestCommandPlan.model_validate(candidate.model_dump())
    clean = TestCommandPlan.model_validate(clean.model_dump())
    plans = [candidate, clean]
    if suite is not None:
        suite = TestCommandPlan.model_validate(suite.model_dump())
        plans.append(suite)
    return _debit(
        store,
        conn,
        run,
        (
            ("sandbox_commands", len(plans)),
            ("execution_seconds", sum(plan.timeout_seconds for plan in plans)),
        ),
    )


def _debit(
    store: Store,
    conn: psycopg.Connection[dict[str, object]],
    run: RunState,
    resources: tuple[tuple[str, int], ...],
) -> Budget:
    try:
        remaining = run.remaining_budget
        for resource, amount in resources:
            remaining = consume(remaining, resource, amount)
        return remaining
    except BudgetError as error:
        if str(error) != "BUDGET_EXHAUSTED":
            raise
        state = RunState.model_validate(
            {**run.model_dump(), "failure_reason": "BUDGET_EXHAUSTED"}
        )
        closed = transition(state, Stage.INCONCLUSIVE, expected_version=run.version)
        store._save(conn, closed)
        conn.execute(
            "UPDATE job_leases SET status='done',token=NULL WHERE run_id=%s",
            (run.run_id,),
        )
        raise _Exhausted from None
