"""In-memory checkout ledger containing a deliberately introduced retry bug.

This fixture uses synthetic transactions and performs no real payment operations.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Payment:
    """Record a synthetic successful checkout payment."""

    payment_id: str
    idempotency_key: str
    amount: Decimal


class PaymentService:
    """Create synthetic payments and expose the resulting ledger for inspection."""

    def __init__(self) -> None:
        """Initialize a new, isolated in-memory ledger."""
        self._payments: list[Payment] = []

    def charge(self, *, idempotency_key: str, amount: Decimal) -> Payment:
        """Process a checkout request.

        Contract: repeated requests using the same idempotency key should return
        the original payment without charging again. The implementation below
        intentionally violates that contract for the ReplayForge benchmark.

        Raises:
            ValueError: If the key is empty or the amount is not positive.
        """
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be positive and finite")

        # BUG: every request adds a new charge, even when its key was used before.
        payment = Payment(
            payment_id=f"pay_{len(self._payments) + 1:04d}",
            idempotency_key=idempotency_key,
            amount=amount,
        )
        self._payments.append(payment)
        return payment

    @property
    def payments(self) -> tuple[Payment, ...]:
        """Return an immutable snapshot of committed synthetic charges."""
        return tuple(self._payments)
