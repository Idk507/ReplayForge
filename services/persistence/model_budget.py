"""Commit request-bound reservations or durable exhaustion before network I/O."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from packages.contracts.budget import Budget
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
    try:
        remaining = consume(run.remaining_budget, "model_calls")
        remaining = consume(
            remaining, "execution_seconds", dispatch.request.timeout_seconds
        )
        return consume(
            remaining,
            "estimated_spend_microusd",
            dispatch.cost.estimated_spend_microusd,
        )
    except BudgetError:
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
