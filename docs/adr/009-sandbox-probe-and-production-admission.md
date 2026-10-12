# ADR-009: Use REST for M0 and keep repository execution disabled

Status: accepted for M0 implementation, 2026-10-12.

## Context

M0 requires an actual remote Python smoke run with isolation and termination
evidence. Nebius now documents Sandboxes REST endpoints. The SDK documentation
and quickstart describe different construction interfaces, and the documented
SDK does not expose all REST network/lifecycle controls. Use the documented REST
surface through the already pinned HTTPX dependency; do not guess SDK behavior.

Official sources inspected on 2026-10-12:

- https://docs.tokenfactory.nebius.com/sandboxes/start/set-up-access
- https://docs.tokenfactory.nebius.com/sandboxes/start/choose-an-interface
- https://docs.tokenfactory.nebius.com/sandboxes/concepts/execution-lifecycle
- https://docs.tokenfactory.nebius.com/sandboxes/guides/configure-networking-and-secrets
- https://docs.tokenfactory.nebius.com/sandboxes/operate/security-and-data-handling
- https://docs.tokenfactory.nebius.com/sandboxes/cookbook/verify-with-networking-disabled
- https://docs.tokenfactory.nebius.com/api-reference/sandboxes/instances/spawn-a-new-container-instance
- https://docs.tokenfactory.nebius.com/api-reference/sandboxes/operations/get-an-operation-status
- https://docs.tokenfactory.nebius.com/api-reference/sandboxes/operations/cancel-an-operation

## Decision

Implement a narrow typed SandboxBackend probe port: start_probe, inspect and
terminate. Its real REST implementation submits only a fixed application-owned
Python program, with literal argv and shell=false. It cannot run a model command,
fetch a repository, apply patches, upload files, or execute host code. The full
production create/execute/apply-patch/collect-artifact interface remains deferred
until its safety and provenance requirements can be satisfied.

Pin an operator-approved provider image UUID. The image must already exist and
contain /usr/local/bin/python, an accessible /tmp, and Linux /sys/class/net.
Do not import an image automatically or accept mutable tags. Require a non-root
UID/GID, networking.enabled=false, an empty guest environment, preserve_env=false,
disposable=true, a 15-second guest timeout, bounded output and writable layer.

The fixed guest program executes python -V and observes UID/GID, interfaces,
Docker socket presence and known credential environment names. It prints no
environment values. The client validates request metadata, operation/image
identity, terminal status, process exit/signal/timeout and output encoding before
accepting the observation. Missing evidence is not a pass. Correlate known work
through its returned UUID; never follow provider Location URLs.

Default CLI execution is offline. --live authorizes one potentially billable
synthetic operation in the selected project; account activation and credentials
are still required. The current implementation session does not make that call.

Submit at most once. A lost/malformed POST response means S_SUBMISSION_UNKNOWN,
not an invitation to retry. Preserve the ambiguous outcome and require operator
reconciliation. After a known submission, bounded polling failure triggers
cancellation and a separate bounded status check, even when cancellation fails
or races with normal completion. No cancellation is sent for unrelated IDs.

## Consequences and admission limits

PASSED is a synthetic probe result, never a repair VERIFIED verdict. Offline fake
HTTP responses cannot satisfy any live milestone gate. A terminal operation
confirms compute finalization according to the service contract; it does not
prove deletion of provider logs, uploads or retained input images.

Image UUID is not an OCI SHA256 digest. The public REST schema documents a
writable-layer cap but does not establish ReplayForge's required hard CPU,
memory, PID, read-only-root, filesystem/host-mount, retention or deletion controls.
Do not add arbitrary repository execution until these are confirmed and tested,
or choose a designated remote Linux runner meeting the architecture contract.

The report includes only an allowlisted guest observation, local correlation ID,
UTC timestamp, owned operation/image UUIDs, stable errors and an observation
digest. The hashed canonical observation is embedded in the report; it is not
yet persisted in an artifact store or signed by a runner identity. Run-level
OpenTelemetry/persistence belong to subsequent control-plane work. No claim of
production runner readiness or full M0 completion follows from this code unit.
