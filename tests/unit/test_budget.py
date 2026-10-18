"""Budget admission must fail before any provider action."""

import pytest
from pydantic import ValidationError

from packages.contracts.budget import Budget
from packages.policies.budget import BudgetError, consume


@pytest.mark.parametrize(
    "resource,limit",
    [
        ("model_calls", 15),
        ("sandbox_commands", 20),
        ("research_searches", 2),
        ("patch_attempts", 3),
        ("execution_seconds", 900),
        ("estimated_spend_microusd", 2_000_000),
    ],
)
def test_exact_limit_then_denial(resource: str, limit: int) -> None:
    budget = Budget()
    exhausted = consume(budget, resource, limit)
    with pytest.raises(BudgetError, match="BUDGET_EXHAUSTED"):
        consume(exhausted, resource)
    assert getattr(budget, resource) == limit
    assert getattr(exhausted, resource) == 0


@pytest.mark.parametrize("amount", [0, -1, True, 1.5])
def test_invalid_consumption(amount: object) -> None:
    with pytest.raises(BudgetError, match="INVALID_AMOUNT"):
        consume(Budget(), "model_calls", amount)  # type: ignore[arg-type]


def test_unknown_resource_and_construct_bypass_fail() -> None:
    with pytest.raises(BudgetError, match="UNKNOWN_RESOURCE"):
        consume(Budget(), "unbounded_shell")
    with pytest.raises(ValidationError):
        consume(Budget.model_construct(model_calls=-1), "model_calls")


def test_time_and_spend_reservations_are_integer_bounded() -> None:
    budget = Budget()
    reserved = consume(budget, "execution_seconds", 60)
    reserved = consume(reserved, "estimated_spend_microusd", 125_000)
    assert reserved.execution_seconds == 840
    assert reserved.estimated_spend_microusd == 1_875_000


@pytest.mark.parametrize(
    "values",
    [
        {"model_calls": -1},
        {"research_searches": 3},
        {"execution_seconds": 901},
        {"estimated_spend_microusd": 2_000_001},
        {"model_calls": True},
        {"shell": 1},
    ],
)
def test_bad_checkpoint_budget(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Budget.model_validate(values)
