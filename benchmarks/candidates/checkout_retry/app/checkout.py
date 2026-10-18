"""Candidate repair for the synthetic checkout retry fixture."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Charge:
    checkout_id: str
    amount_cents: int


class CheckoutGateway:
    def __init__(self) -> None:
        self.charges: list[Charge] = []

    def charge(self, checkout_id: str, amount_cents: int) -> None:
        self.charges.append(Charge(checkout_id, amount_cents))


class CheckoutService:
    def __init__(self, gateway: CheckoutGateway) -> None:
        self.gateway = gateway
        self._completed: set[str] = set()

    def complete(self, checkout_id: str, amount_cents: int) -> None:
        """Charge each checkout once while preserving independent checkouts."""
        if checkout_id in self._completed:
            return
        self.gateway.charge(checkout_id, amount_cents)
        self._completed.add(checkout_id)
