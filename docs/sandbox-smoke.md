# M0 remote sandbox smoke

The fixed synthetic probe passed live on 2026-10-15. It observed Python 3.12.13,
UID/GID 1000, loopback-only interfaces, no Docker socket and no credential-named
environment variables. The correlated operation reached a confirmed terminal
state. See evidence/m0-live-sandbox-attempt.json and m0-live-acceptance-checks.json.
No repository execution is available; this does not establish all runner controls.

## Windows operator setup

Run `uv sync --locked` from ReplayForge. Set these variables securely in your
process environment (no secret values belong in command history or the repo):

- NEBIUS_API_KEY: a least-privilege Token Factory project credential.
- NEBIUS_PROJECT_ID: the activated sandbox project, not NEBIUS_AI_PROJECT.
- SANDBOX_IMAGE_UUID: an existing approved, immutable image UUID containing
  /usr/local/bin/python and Linux /sys/class/net. Do not use a tag or import one
  automatically. Image contents must be trusted for this synthetic probe.
- SANDBOX_REMOTE_URL: optional; only the documented HTTPS Token Factory sandbox
  origin/path is accepted. Redirects, arbitrary origins and proxies from the
  process environment are disabled.

`uv run python -m scripts.check_sandbox` validates configuration and returns
NOT_RUN without creating an HTTP client. Missing configuration returns
S_CONFIG_MISSING. Both return nonzero. The entry function returns 2; some command
wrappers may normalize nonzero exits differently.

After separately authorizing the possible charge, use:

```powershell
uv run python -m scripts.check_sandbox --live
```

One fixed guest probe runs with shell=false, network disabled, UID/GID 1000,
empty environment, no filesystem persistence, a 15-second guest execution cap,
4 KiB output caps and a 64 MiB writable-layer cap. The guest program runs Python
with -V and emits only safe synthetic observations. No control-plane credential
is sent in its inputs. Image-defined environment is not inherited with shell=false.

The client checks exact request metadata, operation/source-image correlation,
service terminal status, process result, network interfaces, UID/GID, Docker
socket absence and credential-name absence. Raw stdout/stderr and provider error
bodies are not output. A PASSED report includes the validated observation and
its canonical JSON SHA256; save the safe report as operator evidence. The hash
uses sorted keys, compact JSON separators and UTF-8 encoding. This is embedded
evidence, not an artifact-store integration.
Reports explicitly label evidence_origin as TEST_TRANSPORT for injected offline
transports, REMOTE_SERVICE for the real HTTP path, or NOT_RUN. A test transport
result cannot be used as evidence of a live sandbox operation.

Polling is bounded by 20 reads and a 45-second wait deadline. Cancellation adds
at most one DELETE and five confirmation reads with a separate 10-second wait
deadline. Per-request connect/read/write/pool timeouts and a response progress
deadline also apply. In-flight request time can extend a wait deadline. Response
body size is capped at 64 KiB and compressed responses are rejected.

S_SUBMISSION_UNKNOWN means accepted work may exist without a returned UUID. Do
not immediately repeat the command; reconcile project operations first. A known
UUID is retained in failure reports. A cancellation acknowledgement alone never
sets compute_terminal_confirmed; a correlated terminal status must be observed.
An unconfirmed terminal state requires operator inspection. No automatic write
retry occurs.

## Capability and acceptance matrix

| Requirement | Implementation | Live evidence |
|---|---|---|
| Fixed Python -V remote work | REST template | Not run |
| Non-root and empty guest environment | Explicit spawn settings + guest checks | Not run |
| No guest network interface except loopback | Explicit setting + guest check | Not run |
| Known-operation cancellation | DELETE + correlated terminal polling | Not run |
| Safe output and observation hash | Offline contract/unit tests | Local only |
| Image source identity | Immutable provider UUID | No OCI digest verification |
| Hard CPU/memory/PID caps and read-only root | Not established by this adapter | Blocked for repository workloads |
| Provider log/input-image deletion | Not guaranteed by disposable runs | Unverified |
| Arbitrary repo execution, patches, signed artifacts | Disabled/unimplemented | Unverified |

The capability table above describes the original pre-live baseline. The fixed
Python run, non-root/empty credential environment, loopback-only networking and
safe observation hash now have live evidence. Cancellation was not exercised
live because normal completion reached terminal state; offline cancellation
tests are separate. Terminal compute does not prove image/log deletion.

M0 full isolation/cleanup and the M2 production runner remain open. Backend extension requires provider
capability evidence or an alternative remote Linux runner, not relaxing these
requirements. See ADR-009 and the implementation log for actual local checks.
