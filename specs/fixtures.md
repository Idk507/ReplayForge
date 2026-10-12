# Benchmark Fixture Contracts

Checkout fixture: Python FastAPI, SQLite test database, endpoint accepts checkout request and idempotency key. Buggy branch inserts second logical charge after retry. Hidden oracle asserts sequential and concurrent retries yield exactly one charge record and equivalent response for the same key, while different keys remain independent. Use synthetic payments only; never process real cards.

Aggregation fixture: Python/pandas ETL with order lines and joined dimensions. Buggy branch doubles sums after join or groups by outdated column. Hidden oracle asserts per-day revenue matches synthetic ledger, handles nulls/duplicates and schema alias migration. All sample sales data fictional.

Concurrency fixture: shared state update with deterministic barrier/latch to amplify lost-update. Hidden oracle asserts count matches accepted increments and cancellation does not corrupt state. Do not rely entirely on nondeterministic thread timing; run multiple seed conditions.

Each fixture includes pinned commit, README, failing incident, isolation policy, dependency lock, public test suite, hidden oracle separate from agent workspace, expected failure signature and benchmark manifest. Collect no personal or production data.
