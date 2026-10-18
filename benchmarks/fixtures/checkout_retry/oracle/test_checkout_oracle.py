import unittest

from app.checkout import CheckoutGateway, CheckoutService


class CheckoutOracleTest(unittest.TestCase):
    def test_distinct_checkouts_remain_chargeable_after_idempotency_fix(self) -> None:
        gateway = CheckoutGateway()
        service = CheckoutService(gateway)
        service.complete("checkout-a", 1999)
        service.complete("checkout-b", 1999)
        self.assertEqual(
            [(c.checkout_id, c.amount_cents) for c in gateway.charges],
            [("checkout-a", 1999), ("checkout-b", 1999)],
        )
