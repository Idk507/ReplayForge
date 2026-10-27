# ReplayForge checkout retry fixture

A deliberately faulty, **synthetic** payment service for testing an autonomous incident-to-patch workflow. No network, payment gateway, database, secrets, containers, or credentials are needed.

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

Expected BEFORE repair: **one failed test**, `test_retry_does_not_create_duplicate_charge`; the other five tests should pass. The test suite is intentionally RED before repair, so nonzero pytest exit status is expected.

## Incident to submit to ReplayForge

**Title:** Checkout retry creates duplicate payment after client timeout

**Incident:** A client submits order `order-101` for 499.00. It doesn't receive the response due to a network timeout, so it retries with the same idempotency key. The checkout ledger contains two payments for one logical order. This could lead to duplicate charges in a real payment system. Reproduce the defect in `tests/test_payments.py`, fix the smallest possible area in `checkout/payments.py`, and rerun all tests. Do not delete/modify the regression test or weaken validation. Provide before/after test evidence and a patch diff.

**Expected invariant:** For the same nonempty idempotency key, retrying with the same amount must return the same payment and keep the ledger count unchanged. Distinct keys must create separate payments.

## ReplayForge submission steps

1. Publish this fixture as a **separate public test repository** under your account (e.g. `replayforge-checkout-demo`). Keep ReplayForge's own implementation repository separate.
2. Record the exact buggy commit SHA and keep the baseline immutable.
3. Install/authorize the ReplayForge GitHub App for **only this fixture repository** and register/select that repository inside ReplayForge.
4. Paste the incident above, choose the buggy commit, and submit the run with the authorized repo identity.
5. Observe baseline test failure, candidate patch, clean replay, deterministic verification verdict, and retained evidence. Only call the repair VERIFIED if the required actual gates succeed.
6. Review and approve the exact diff before any remote PR operation. Never automatically merge it.

**Note:** This fixture is intentionally vulnerable to duplicate synthetic charges. It must not be connected to a real payment provider or used in production.
