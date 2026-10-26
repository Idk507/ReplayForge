# Durable Nebius source materialization

The source packer now connects to a concrete Nebius REST upload, saved image
and independent archive readback. This is not repository test execution, runner
attestation, a VERIFIED repair or M3 completion.

## Data and lifecycle

prepare_upload verifies original source bytes against the canonical retained
SourceManifest (Git blob ID, SHA256 and length), plus a separately retained
ReproductionArtifact. The proposed test cannot overwrite an original file. All
members are regular0644 files or0755 directories; names/count/content/wire size
are bounded by existing archive_identity. Neither source nor test is executed on
the host. The fixed remote Python -I -S extractor receives archive bytes via
base64 stdin, never shell interpolation. Guest networking and inherited env are
disabled, uid/gid1000, a15second timeout and1MiB layer bound are requested. These
requests are not proof of every required runner control.

materialize_once reloads retained manifest/reproduction, invokes its trusted
current-access callback, checks current DB owner/consent/lease/version/stage and
commits an immutable intent plus command/time budget before POST. The source
broker callback remains an internal trusted deployment dependency. No public
upload/execute endpoint or automatic stage scheduler is enabled by this slice.

Migration0029 adds append-only materialization_intents, materialization_operations
and materialization_results. Runtime gets SELECT/INSERT only. One original
workspace preparation is admitted per run. Unknown submission remains sticky;
new materializer instances cannot submit it again. Known IDs permit GET-only
collection on restart. Current fences apply to every read/retain action. Receipt
reads keep the original observation time and intent expiry. A cached result has
its canonical checksum and source/operation/image/origin binding rechecked.
No TTL or budget is renewed, and no VERIFICATION state is granted.

The adapter requires terminal operation SUCCESS, process exit0/no timeout/signal,
a distinct saved result image and an independently downloaded workspace archive
matching the expected content identity. Service SUCCESS alone is insufficient.
SSE/API events expose only stage/version/event names, never source or test bytes.
Closing a client releases local resources; it does not delete the saved image.
Downgrade refuses nonempty intent custody; archival review is required first.
No application DB migration/grants were applied during this acceptance.

## Live acceptance:2026-10-25

A single explicitly authorized inert fixture probe ran through the actual REST
adapter and durable controller in a disposable PostgreSQL database. Two files,
36 expanded bytes, were uploaded and independently measured. Only the fixed file
extractor ran in Nebius. No test, application source, inference or GitHub write
ran. Metadata source/reproduction identity is explicitly SYNTHETIC_INERT_FIXTURE;
it is not a GitHub source or real repair acceptance. Evidence:

- docs/evidence/materialization-live-result.json
- docs/evidence/materialization-live-attempt.jsonl

scripts.accept_materialization is operator acceptance scaffolding using tiny test
fixtures, not a production worker. Default startup returns NOT_RUN/exit2 without
config/provider/DB I/O. --live additionally requires a named rf_test_* DB. An
exclusive fsynced local attempt file precedes the only possible POST, preserving
no-resubmission across disposal of the test DB. This invocation must not be
repeated as routine development testing. Unknown attempt files require owned
operation investigation, never deletion to manufacture a new dispatch permit.
The known saved image remains subject to provider retention; deletion is not
claimed. The disposable DB/roles were cleaned after the successful observation.

## Scope still open

A measured prepared workspace now exists for the inert acceptance fixture. It is
unsigned content provenance. Before arbitrary repository baseline/candidate/clean
replay, independently establish runner OCI/environment/configuration identity,
actual capability assessment and collector trust. Neither a new signing key nor
provider UUID can replace those observations. Complete admitted worker wiring,
seven actual repair gates, subsequent exact human review/live approved PR and
hidden variation evaluation remain required; original M3/M4 exits are unchanged.

## Official sources

- [Instance request](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/instances/spawn-a-new-container-instance): stdin encoding, networking, uid/gid, saved execution and explicit environment.
- [Execution lifecycle](https://docs.tokenfactory.nebius.com/sandboxes/concepts/execution-lifecycle): fresh compute, saved image reuse, terminal operation versus process result, disconnect recovery.
- [Archive endpoint](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/inspect/download-a-file-or-a-directory-from-image-as-tar-archive): streamed POSIX PAX tar, rooted at selected directory. Validation never extracts provider bytes on the host.
