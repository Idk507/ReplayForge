"""Pure action reservation; caller must commit atomically before dispatch."""

from packages.contracts.budget import Budget


class BudgetError(Exception):
    """Safe admission failure, never a reason to retry the denied action."""


def consume(budget: Budget, resource: str, amount: int = 1) -> Budget:
    """Debit attempted work; failed provider calls must not refund attempts.

    This does not authorize execution or persist counters. Time and spend are
    integer units in the checkpoint and use the same fail-closed debit rule.
    Concurrent workers require a database transaction around the updated state.
    """
    budget = Budget.model_validate(budget.model_dump())
    if resource not in Budget.model_fields:
        raise BudgetError("UNKNOWN_RESOURCE")
    if type(amount) is not int or amount <= 0:
        raise BudgetError("INVALID_AMOUNT")
    data = budget.model_dump()
    remaining = data[resource]
    if amount > remaining:
        raise BudgetError("BUDGET_EXHAUSTED")
    data[resource] = remaining - amount
    return Budget.model_validate(data)
