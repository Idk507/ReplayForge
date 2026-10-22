# Prepared-image Nebius REST backend

`services.runner.prepared.PreparedNebiusBackend` implements the submission port
used by the durable replay dispatcher. It operates on an **already prepared,
immutable image**, with an exact `PreparedImageBinding` supplied by trusted
materialization code. It does not clone repositories, upload directories or
apply model patches on the host. Browser/model input cannot be used to construct
the binding as proof of workspace preparation.

## Provider sources checked 2026-10-22

- [Execution model](https://docs.tokenfactory.nebius.com/sandboxes/overview): each
  operation starts fresh compute from an image; a saved image retains filesystem
  state, not processes. UUID and OCI digest are different identities.
- [Interface selection](https://docs.tokenfactory.nebius.com/sandboxes/start/choose-an-interface):
  REST returns an operation identity immediately; SDK 0.3.6's waiting workflow is
  not substituted for the durable submission controller.
- [Spawn schema](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/instances/spawn-a-new-container-instance):
  explicit image, command/args, environment, cwd, uid/gid, timeout, networking and
  persistence settings. The existing closed REST plan serializer is reused.
- [Security/data handling](https://docs.tokenfactory.nebius.com/sandboxes/operate/security-and-data-handling):
  supply networking.enabled=false on every relevant spawn. Discarding filesystem
  changes does not prove deletion of uploads, operation records or logs.

## Implemented path

Construction requires a fixed-destination SandboxConfig, trusted prepared-image
binding, explicit capability attestation and configured public-key verifier.
Live construction requires `allow_live=True`; dispatch separately requires
explicit live authorization. Custom HTTP transports are always TEST_TRANSPORT.
Construction performs no provider request and never loads the project's `.env`.

Submission revalidates the exact plan hash, execution ID, expected tree, image
UUID/digest and assessed runner/configuration identities. Capability verification
is repeated at the transport boundary. It sends the application-owned plan with
network disabled, no shell, no inherited/preserved environment, non-root uid/gid,
fixed timeout and bounded output. No source or credentials are embedded in argv.
The one-use marker is set before the POST, including on malformed/uncertain
responses. Timeout/transport/schema uncertainty becomes safe TimeoutError for
the durable controller, whose retained claim prevents resubmission after restart.
Authentication/redirect denials remain explicit stable RunnerError codes.

The backend supports one bounded status read and an explicit cancellation request
for its owned operation only. Responses must match both operation and image
identities. Cancellation acceptance does not prove terminal compute. Context
exit closes HTTP resources; it does **not** silently cancel submitted work or
claim resource teardown. The caller must observe terminality and perform owned
cleanup. The readonly origin/runner/configuration port properties match the
dispatcher without exposing mutation as part of that interface.

## Remaining runtime requirements

PreparedImageBinding is trusted configuration, not a new signature or an image
assessment. A real materializer must establish the actual OCI digest/provider
UUID mapping, pinned source, admitted patch, unchanged test, prepared tree and
runtime configuration. This module cannot establish those facts from a UUID.
The signing authority must remain outside guest/model control. No production
image, preparation service or signing identity is installed by this increment.

The current command template discards filesystem changes. Its JUnit file at
`/tmp/replayforge-result.xml` is therefore **not available for post-run image
inspection**. A separately designed, admitted report-export/retention path is
required before this backend can feed the signed receipt producer. Do not infer
passing tests from operation SUCCESS, or substitute guest stdout assertions for
authenticated reports. No VERIFIED result or live M3 completion is claimed.

This closes the concrete REST submission/owned-status/cancellation adapter for
pre-materialized images. General source materialization, artifact collection,
bounded orchestration/cleanup and live seven-gate acceptance remain open. The
existing SDK runner and M0 smoke command were preserved and are not enabled as
an unsafe fallback. No authentication UI or API changes were made.

## Observed acceptance — 2026-10-22

Base `afe7788`; the implementation was concurrently checkpointed as `d56714e`
without changing the tested backend bytes. This follow-up also formats the
committed smoke.py trailing whitespace, with no behavior change.

- `uv run pytest tests/contract/test_prepared_backend.py
  tests/unit/test_rest_plan.py -q
  --junitxml=docs/evidence/m3-prepared-backend-contract.xml`: exit 0, **21 passed**,
  zero failures/errors/skips. Small mocked HTTP exchanges cover exact settings,
  denied binding/signature/live calls, sticky timeout/schema/auth/redirect outcomes,
  ownership and mismatched status responses.
- Initial focused PostgreSQL composition: **1 passed**, 1021 deselected, exit 0
  (`m3-prepared-dispatch-focused.xml`). The full run includes the subsequently
  added timeout path across backend/controller restart, with exactly one POST.
- Final full disposable-PostgreSQL regression: exit 0, **1026 passed**, zero
  failures/errors/skips, **289.65 seconds**;
  `docs/evidence/m3-prepared-backend-postgres.xml`. One upstream Starlette/AnyIO
  deprecation warning remains.
- Global Ruff lint and formatting passed (186 files), strict mypy passed
  (93 source files), and diff/scoped credential-pattern checks passed. No
  dependency/UI changes, so their audits/browser checks were not repeated.
  The offline M2 checker still returns M2_FIXTURE_ACCEPTED.

The first full run had 1025 passes and one timeout in the existing subprocess
crash/rollback test's 10-second child-process guard. It is retained losslessly as
`docs/evidence/m3-prepared-backend-first-run.xml.gz`. A separate child import
probe completed in 0.58s and the unchanged targeted test passed in 3.38s
(`m3-worker-crash-rerun.xml`). The cause could not be reproduced and is not
claimed fixed. No timeout/product constraint was relaxed or assertion removed.

Reproduce the full regression (prompts for the local administrator password):

```powershell
uv run python -c "import sys,subprocess; from scripts import test_postgres as t; call=subprocess.run; subprocess.run=lambda args,**kw:call(args,**{**kw,'timeout':420}); sys.argv=['test_postgres','--evidence-name','m3-prepared-backend-postgres.xml']; sys.exit(t.main())"
```

The 420-second ceiling is only the outer suite harness ceiling; the crash test's
own 10-second limit and execution-policy timeouts were unchanged. The harness
cleaned its random task-owned local databases/roles. The local database password
was supplied in memory only. No live inference, sandbox, Tavily or GitHub request,
deployment, merge or remote write occurred. Authentication was left unchanged.
