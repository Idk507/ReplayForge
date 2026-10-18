import unittest

from app.checkout import CheckoutGateway, CheckoutService


class CheckoutRetryTest(unittest.TestCase):
    def test_retry_does_not_create_a_second_charge(self) -> None:
        gateway = CheckoutGateway()
        service = CheckoutService(gateway)
        service.complete("checkout-42", 1999)
        service.complete("checkout-42", 1999)
        self.assertEqual(len(gateway.charges), 1)
