# ADR-008: Separate M0 connectivity proof from worker inference

Status: accepted for the first implementation unit, 2026-10-12.

M0 needs an inference smoke test before the domain/orchestrator exists. Build a
small HTTPX/Pydantic provider module with a fixed synthetic prompt and a module
CLI. It checks exact NVIDIA Nemotron catalog membership before inference;
missing configuration, malformed/truncated responses and provider failures are
explicit errors. No fallback model is selected.

Only documented Token Factory HTTPS hosts and /v1 paths are accepted. Disable
redirects, environment proxies and automatic retries; cap response bytes and
request timeouts. Output only locally generated correlation ID, UTC timestamp,
endpoint/model, HTTP success, latency and observed token counts. Missing usage
and unconfigured cost remain null. Never output provider text, headers or error
bodies. The probe is not exposed as an API endpoint or model tool.

Default execution validates configuration without network. --live deliberately
enables a potentially billable request. The implementation session does not have
authorization to trigger paid resources. Offline HTTP transport tests are
clearly labeled and cannot satisfy M0's live inference gate.

Use the regional endpoint/model from the official Nemotron example as defaults;
also accept the general endpoint documented in the quickstart. Account catalog
membership and successful inference, not documentation, determine availability.

The beta Sandboxes service is a feasibility candidate, not an operational
integration. Its per-operation lifecycle must be reconciled with the runner
port, image digests, deny-network policy, termination and artifact provenance
before adoption. No local execution fallback is allowed.

The production gateway will separately implement run budgets, concurrency,
bounded transient retries, schema-constrained agent output and durable usage
events. This smoke module must not be mistaken for those completed features.
