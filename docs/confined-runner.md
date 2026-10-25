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

Evidence: `pinned-image-import*-attempt.jsonl`,
`pinned-image-import*-observation.json`, `python-oci-source.json` under
`docs/evidence/`. This proves failure for this reference, not all Nebius imports.
Billing was not returned. Keep guards; do not silently substitute a mutable tag.
Next: working digest import/provider guidance, pinned pytest environment,
actual confined baseline/candidate/clean replay and all seven gates.

References:

- [Nebius import](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/images/import-a-container-image-from-a-registry): flattened rootfs, not OCI layers/configuration.
- [Official SDK](https://github.com/nebius/contree-sdk): URL normalization/import.
- [Seccomp](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html)
- [cgroupv2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- [Mount](https://man7.org/linux/man-pages/man2/mount.2.html)

Tests: `test_confinement.py`, `test_confined_producer.py`,
`test_receipt_producer.py`, `test_durable_producer.py`. Their invented profiles
are synthetic contract data; retained JUnit records actual local outcomes.
