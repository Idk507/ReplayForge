# Known-operation recovery

`services.orchestrator.operation_recovery.recover_baseline_operation` performs
one read of an already persisted baseline operation. This internal worker
function is not an API endpoint, dispatcher, test verifier or polling daemon.

The caller supplies the active lease, owner and baseline execution ID. The
operation UUID comes exclusively from owner-scoped durable journal history;
unknown submissions cannot be looked up by guessed IDs or resubmitted. A
history belonging to a different run or transport is rejected before HTTP.
The existing REST client's destination, header, timeout, response-size and
redirect guards apply. The client is always closed, including on failures.

Each call issues at most one GET of `v1/operations/{uuid}`. There are no POST,
DELETE, automatic retries or sleeps. Pending states return bounded metadata.
SUCCESS, FAILED and CANCELLED append COMPUTE_TERMINAL through the existing
transactional journal after rechecking the live lease and checkpoint version.
Concurrent terminal writes reconcile through that journal. Restarting after a
terminal append returns ALREADY_TERMINAL without HTTP; provider_status is null
because the journal is historical rather than a fresh provider observation.
Database or lease errors never report a successful terminal append.

Provider metadata and raw output are omitted from RecoveryResult. Real HTTP
observations remain UNVERIFIED_ADAPTER_INPUT; injected transports are explicitly
TEST_TRANSPORT and cannot consume production-provider history. Neither form
authenticates a test result, proves image UUID to OCI digest binding, proves
deletion/retention, refunds a budget or advances the repair stage. SUCCESS does
not imply guest exit code zero. The journal does not retain provider process
results; signed report delivery remains a separate prerequisite.

Cancellation or revoked consent must not prevent observing already running
owned work. This read-only path consequently does not grant or require renewed
execution consent. A checkpoint change during the read rejects the observation;
the trusted caller can reconcile later under the current lease. Missing,
expired or unknown operation history stops before network access.

The default polling worker remains collection-only. An operator must explicitly
wire and schedule this internal function after production runner admission,
submission and operation-ID retention are implemented. Transport errors leave
the known journal identity intact; retry policy belongs to that bounded worker,
not to an HTTP retry loop. No paid acceptance call is needed to test this path.

## Sources checked 2026-10-19

- [Execution lifecycle](https://docs.tokenfactory.nebius.com/sandboxes/concepts/execution-lifecycle):
  client waits and remote compute have separate lifetimes; inspect the operation
  before retrying and interpret process results separately from service status.
- [Choose an interface](https://docs.tokenfactory.nebius.com/sandboxes/start/choose-an-interface):
  REST supports recording an operation ID and inspecting it later.
- Existing `services/runner/remote.py` GET route and bounded response contract;
  `services/persistence/operations.py` append-only identity/fencing contract.

Contract tests use the real disposable PostgreSQL harness and synthetic HTTP
responses. They are not evidence of a live admitted production runner.
