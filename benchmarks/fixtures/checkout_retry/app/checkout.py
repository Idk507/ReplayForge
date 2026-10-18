"""Synthetic checkout service with an intentional retry-idempotency defect."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Charge:
    checkout_id: str
    amount_cents: int


class CheckoutGateway:
    """In-memory deterministic fixture; intentionally charges every retry."""

    def __init__(self) -> None:
        self.charges: list[Charge] = []

    def charge(self, checkout_id: str, amount_cents: int) -> None:
        self.charges.append(Charge(checkout_id, amount_cents))


class CheckoutService:
    def __init__(self, gateway: CheckoutGateway) -> None:
        self.gateway = gateway

    def complete(self, checkout_id: str, amount_cents: int) -> None:
        """Complete a checkout; baseline defect: retries duplicate a charge."""
        self.gateway.charge(checkout_id, amount_cents)
