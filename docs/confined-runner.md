# Confined measured runner

Status: internal opt-in implementation, locally tested with synthetic profiles.
The earlier remote diagnostic establishes kernel prerequisites only; it did not
execute this workload template. This feature does not close M3 or M4.

## Execution and collection

`services/runner/confinement.py` constructs a fixed remote supervisor exclusively
for `PYTEST_FILES_MEASURED_V1`. No guest code executes on the developer/API host.
Root performs setup inside the remote VM; pytest runs as uid/gid1000 with no
supplementary groups, effective capabilities, inherited environment or network.

The template applies read-only inherited mounts, no-new-privileges, x86_64
seccomp, one-core CPU quota, 128MiB memory with swap disabled and 16 PIDs.
Workspace and temporary tmpfs each have 16MiB/256-inode limits. Combined output
is capped at64KiB and child time at55seconds within the60second provider deadline.
Final workspace bytes are frozen before unmounting, then restored into the saved
image for independent measurement. Traversal rejects links/special files and
caps data at64KiB/256entries. JUnit uses no-follow/nonblocking regular-file reads.

`ConfinedMeasuredBackend` preserves existing admission and adds a complete spawn
payload hash: `confinement_configuration_ref(plan,image_uuid,execution_id)`.
Hashing a template is not assessment of its controls. The independent assessor
must establish OCI/environment provenance, all22controls and collector trust.
Construction/default startup performs no I/O. Privileged setup and live dispatch
have separate explicit internal opt-ins, never browser/model authority.

A protected descriptor records child identity/confinement and closes before
pytest exec. Collection reads the protected record from the owned result image
and validates exact run/execution/plan/argv/environment, controls/limits and
supervisor-versus-child exit agreement before signing. Missing or mismatched
reports produce no receipt. The ordinary prepared backend remains direct-child.
Nebius `signal=-1` is normalized to the signed contract's no-signal `None`;
actual terminating signals remain intact.

`DurableConfinedBackend` adds existing `ProducerJournal` candidate/clean fences:
premeasurement commits before POST, only known operations restore, unknown
submission stays sticky, and receipts commit before return. Cached reads retain
original time and perform no HTTP. Baseline durable collector orchestration and
automatic prepared-environment registration remain to be implemented.

## Limitations

- Measured pytest only. Fork/subprocess tests are unsupported; threads are
  filtered and PID-limited. Anonymous UNIX socketpairs support event loops;
  named socket creation and network connections are denied.
- Linux/x86_64 with cgroupv2, mounts and seccomp is required. Setup fails closed.
- No real capability profile is generated. Prior diagnostic evidence predates
  this complete workload template and cannot attest it.
- Saved result images retain provider data. Client close is neither compute
  termination nor image deletion. Retention acceptance remains open.
- No arbitrary-command public route or automatic publication is enabled.
  A receipt signature alone is neither VERIFIED nor human approval.

## Pinned import finding,2026-10-25

The public Python3.12.12 slim-bookworm linux/amd64 manifest was independently
fetched and SHA256-checked:
`sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301`.
The current provider catalog image has no source OCI identity in inspect metadata.

Two bounded attempts are retained without automatic resubmission:

1. `01a173a2-b3fd-7406-915a-648016764605`: missing URL scheme rejected. Installed
   SDK `OCIReference.from_oci` establishes the required `docker://` normalization.
2. `01a173a3-0a88-765e-91e1-7a4a476328da`: corrected digest URL reached registry
   pull but failed HTTP404. No result image was produced.

One follow-up attempt used the independently recorded multi-platform index
digest `sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c`.
The SDK returned `ApiStatusCodeError` without exposing an operation UUID. The
exact HTTP status was not captured. A later read-only image inventory returned
no images for the interval (`pinned-image-import-index-discovery-v2.json`). The
outcome remains unresolved; the import was not repeated and no image is available
for a trusted runner binding.

Evidence: `pinned-image-import*-attempt.jsonl`,
`pinned-image-import*-observation.json`, `python-oci-source.json` under
`docs/evidence/`. This proves failure for this reference, not all Nebius imports.
Billing was not returned. Keep guards; do not silently substitute a mutable tag.
Next: resolve the provider API error through supported provider diagnostics,
establish an independently qualified pinned pytest runtime and collector, then
run baseline/candidate/clean replay and all seven gates. Do not retry the
recorded index import.

### Diagnostic follow-up, 2026-10-26

Inspection of installed `contree-sdk==0.3.6` source found that
`images._import_image()` starts an operation and then polls internally before
returning the image. The base client wraps import-start calls in a `CircuitRetrier`
for API timeout and rate-limit errors. The high-level `images.import_from()`
path therefore provides no application callback to persist the operation UUID
before polling and can repeat a start request for those transient exception
types. The retained index observation recorded only the exception class; its
status/headers and operation UUID were not retained, so its origin (POST
rejection versus operation-poll failure) cannot be inferred. A local SDK-source
check also found that `OCIReference.from_oci()` sets `ref.tag` to
`python@sha256:<digest>` for the digest reference, and `_import_image()` uses
that as the destination `tag` unless overridden. This may explain the failure,
but remains a hypothesis because the actual response was lost. The new adapter
omits target tag (private import) while preserving the pinned digest in the
registry URL.

Added `services/runner/import_diagnostics.py` and
`services/runner/one_shot_image_import.py`. The latter requires intent and
operation-retention callbacks, makes at most one POST, stores the returned UUID
before polling, and performs bounded GET-only polling. The former records only
allowlisted exception metadata. Fake-transport tests prove redaction, one-shot
uncertain failures and callback order. Evidence:
`docs/evidence/m3-import-diagnostics.xml`, 19 passed. These callbacks are not yet
wired to a production durable journal/runtime admission, and no live image import
has been tested through this adapter; Task 2 remains incomplete. Do not repeat
the provider request until admission/journal integration is reviewed. The
[official Nebius API](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/images/import-a-container-image-from-a-registry)
documents the import as an asynchronous POST returning 201 with an operation
UUID, requires IAM Bearer and Project headers, and notes that imports retain the
root filesystem while omitting most image configuration metadata.

References:

- [Nebius import](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/images/import-a-container-image-from-a-registry): flattened rootfs, not OCI layers/configuration.
- [Official SDK](https://github.com/nebius/contree-sdk): URL normalization/import.
- [Seccomp](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html)
- [cgroupv2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- [Mount](https://man7.org/linux/man-pages/man2/mount.2.html)

Tests: `test_confinement.py`, `test_confined_producer.py`,
`test_receipt_producer.py`, `test_durable_producer.py`. Their invented profiles
are synthetic contract data; retained JUnit records actual local outcomes.

### Image-import journal follow-up, 2026-10-26

Migration0031 and `services/persistence/image_imports.py` add a separate
operator-side sticky import ledger. A project/source-digest claim is committed
before the single POST; the operation UUID is persisted before polling; only
bounded allowlisted diagnostics and terminal provider identity are retained.
Recovery validates the ordered event chain, request/source/project bindings,
checksums and result fields. An uncertain POST or provider rejection remains
non-retryable for that source digest. This ledger is not connected to the
qualified runtime admission/deployment path, and the tests use a fake HTTP
transport. A default-off operator controller/CLI now uses the journal: `--live`
is required to submit a new import, known operations are GET-only, and an
uncertain source digest is never POSTed again. The adapter accepts the typed
journal lifecycle only when
project and request identities match; GET recovery also requires that journal
to hold the exact operation in `OPERATION_KNOWN`. The CLI was not invoked with
`--live`; no provider image import or sandbox execution occurred in this slice.

Evidence: nine importer-journal/controller PostgreSQL tests passed; combined
with the patched-materialization slice, 12 tests passed at
`docs/evidence/m3-import-controller-final.xml`; 32 selected unit tests passed
at `docs/evidence/m3-import-controller-unit.xml`. Ruff, format and targeted
mypy passed. This does not establish source OCI provenance,
capability assessment, signer trust, receipts, execution or M3 completion.

### Latest live diagnosis, 2026-10-26

This supersedes the earlier statement that the import path had only been tested
locally. Under the user's standing bounded-cloud authorization, one fresh
Python platform digest was independently checked against Docker Hub and sent
through the one-shot controller. Local migrations advanced 0027 to 0031. The
provider accepted operation `01a17900-0b82-7006-9584-77d718afd5fa`, then failed
with registry HTTP404 and no result image. The durable terminal record and event
chain recovered correctly. The corrected request omitted a destination tag, so
the target-tag hypothesis is not a sufficient explanation. No retry occurred.

Safe terminal diagnostics now retain a fixed failure classification, upstream
HTTP status and raw-error checksum only. Poll policy is checked before any DB or
provider action; cached results preserve original observation times. Local RED
tests found six failures/11 passes; GREEN selected unit tests passed37 and
PostgreSQL tests passed12, with one upstream warning. Ruff/format/mypy passed.
See `docs/evidence/m3-live-diagnostics-unit.xml`,
`docs/evidence/m3-live-diagnostics-postgres.xml` and the ready-to-send
`docs/m3-nebius-import-support.md`. No repository execution, inference or GitHub
write occurred; provider billing was not returned. M3 remains incomplete.
