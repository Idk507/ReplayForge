"""Public acceptance tests for the synthetic checkout payment service."""

from decimal import Decimal

import pytest

from checkout.payments import PaymentService


def test_first_payment_is_recorded() -> None:
    """A valid first checkout request must create a payment."""
    service = PaymentService()
    payment = service.charge(idempotency_key="order-101", amount=Decimal("499.00"))
    assert payment.payment_id == "pay_0001"
    assert len(service.payments) == 1


def test_retry_does_not_create_duplicate_charge() -> None:
    """A network retry with the same key must return the original payment."""
    service = PaymentService()
    first = service.charge(idempotency_key="order-101", amount=Decimal("499.00"))
    retried = service.charge(idempotency_key="order-101", amount=Decimal("499.00"))
    assert retried == first
    assert len(service.payments) == 1


def test_distinct_orders_create_distinct_payments() -> None:
    """Distinct idempotency keys represent independent orders."""
    service = PaymentService()
    service.charge(idempotency_key="order-101", amount=Decimal("499.00"))
    service.charge(idempotency_key="order-102", amount=Decimal("699.00"))
    assert len(service.payments) == 2


@pytest.mark.parametrize("key,amount", [("", Decimal("10")), ("order-1", Decimal("0")), ("order-1", Decimal("NaN"))])
def test_bad_checkout_input_is_rejected(key: str, amount: Decimal) -> None:
    """Malformed inputs must not create charges."""
    service = PaymentService()
    with pytest.raises(ValueError):
        service.charge(idempotency_key=key, amount=amount)
    assert len(service.payments) == 0
