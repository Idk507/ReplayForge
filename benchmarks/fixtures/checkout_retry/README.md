# Checkout retry fixture

This is a deterministic, synthetic Python fixture for the M2 baseline-reproduction path. The visible test asserts that retrying the same checkout must create one charge. On the immutable baseline, `CheckoutService.complete` records two charges, so the visible test has a behavioral assertion failure.

The oracle checks that an idempotency repair does not suppress independent checkouts. It is stored separately for local development only; it is **not hidden** from this repository checkout. A real benchmark run must package it outside model-visible context and run it only in an independently admitted remote runner.

Suggested sandbox-only commands after a future runner admission: `python -m unittest discover -s visible_tests` on baseline, then `python -m unittest discover -s oracle` on a clean patched checkout. Do not run this fixture on the Windows control-plane host.
