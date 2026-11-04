# Sandbox execution: fallback plan (2026-11-04)

Decision from the user: do not stall on the Nebius sandbox. If a blocker persists
past its time-box, move to the next alternative. Deployment was deleted by the
user; the complete working workflow comes first, redeploy later.

## Why this exists
M3/M4 need untrusted repository code (the generated test, then the patch) to run
in a designated remote Linux sandbox, never on the API host (AGENTS.md). The
Nebius path is blocked: the pinned OCI import failed HTTP 404 after SDK URL
normalization, and the provider catalog image carries no source OCI identity
(`docs/confined-runner.md`). Catalog UUID/tag must never be substituted for a
verified OCI identity. That rule stays; only the provider may change.

## Rule: the provider is replaceable, the acceptance is not
Any provider must satisfy the same contract before it can back M2/M3 evidence:
1. Immutable environment identity recorded (image digest or equivalent measured
   hash). Unknown identity => the receipt says so; it cannot yield VERIFIED.
2. Isolation from the API host and from every other run; no ReplayForge, GitHub,
   vault or inference credentials inside the sandbox.
3. Network egress denied by default (allowlist only, e.g. a pinned wheelhouse).
4. Allowlisted command templates only, hard timeouts, forced teardown, bounded
   output; source and tests treated as data.
5. Measured, content-addressed artifacts and receipts; baseline failure on the
   original commit and clean replay in a second fresh sandbox.
6. Spend and run budgets journaled before the request (existing durable
   pre-POST intent rules apply to every provider).
Changing provider never lowers an M2/M3 exit test, and synthetic/fake adapters
stay labelled and can never produce VERIFIED.

## Ordered options
| # | Option | Isolation | Fit | Main risk |
|---|---|---|---|---|
| A | Nebius sandbox (current) | provider-managed | Already integrated (measured upload, receipts) | OCI import HTTP 404; no OCI identity |
| B | **NVIDIA OpenShell** on a remote Linux host (a Nebius VM keeps NVIDIA+Nebius) | kernel-level isolation; declarative YAML policy for filesystem, process, network, inference | Open source; policy model matches our deny-by-default needs; credentials injected rather than stored in the sandbox | Per public docs it runs a K3s cluster inside Docker, so it needs a remote Linux host we operate; GPU support is labelled experimental; created 2026-02, so maturity and API stability are unverified |
| C | Managed microVM sandbox (e.g. E2B, Firecracker) | own kernel per sandbox | Strongest boundary of the managed options per public comparisons | New vendor/account/spend; new adapter; unknown image-digest story |
| D | Managed gVisor sandbox (e.g. Modal) | user-space kernel | Fast to adopt | Weaker boundary than microVM for fully untrusted code |
| E | Self-managed Linux VM with rootless containers plus gVisor or Firecracker | we choose | Full control, identity measurable by us | Most operational work |

Recommendation: time-box A, then take B. B directly answers the isolation and
policy requirements, can be driven by a thin adapter that runs our existing fixed
extractor and measured pytest supervisor unchanged, and keeps NVIDIA technology
in the story. Use C as the second fallback if B's host setup or policy API proves
unworkable. Prefer swapping the provider over debugging the provider.

## Time-boxes (escalate, do not grind)
- A: one focused attempt to fix the OCI import (registry reference, normalization,
  public vs private registry). If still failing after that, record the evidence
  and stop. Preserve all prior attempt records.
- B: spike of one working day. Exit: on a throwaway Linux host, create a sandbox
  with a deny-all network policy, run the fixed extractor and one tiny inert
  pytest, collect an artifact, tear it down. If this fails, move to C.
- Every spike is a tiny inert probe (no source, no credentials) and is not repeated
  as routine testing.

## Work breakdown
1. `SandboxProvider` adapter boundary around the confined runner (start, upload,
   execute allowlisted template, collect, destroy) with the Nebius implementation
   and a clearly labelled fake behind it. No behaviour change for existing paths.
2. Provider conformance tests: the six contract points above, run against the
   fake locally and against each real provider when authorized.
3. OpenShell adapter spike (needs user authorization: host, spend, account).
4. Wire baseline and clean-replay through the adapter; keep unsigned observations
   unsigned until collector trust bindings exist (`docs/replay-collection.md`).
5. Update `docs/confined-runner.md` with the chosen provider and findings.

## Needs the user's authorization before any external call
A Linux host or provider account, the spend cap, and the first real repository.
Nothing here has been run against OpenShell yet; the capabilities above come from
public documentation and are unverified for our workload.

## Sources
- [NVIDIA OpenShell overview](https://docs.nvidia.com/openshell/latest/about/overview)
- [Agent sandbox providers compared (Fly.io)](https://fly.io/learn/agent-sandbox-providers/)
