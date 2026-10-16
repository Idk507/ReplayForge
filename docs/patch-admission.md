# Bounded patch admission before sandbox application

services.runner.patches.admit_patch is a pure preflight gate. It parses text and
returns immutable metadata; it never invokes git, writes files, applies changes
or executes source. ADMITTED_FOR_CHECK and can_authorize_execution=false are
deliberately distinct from an applied patch or a verified repair.

Input must match the trusted run's source commit, source manifest reference,
baseline result reference and reproduction test reference. The current run must
be PATCHING with owner consent, no cancellation request and remaining patch
budget. References bind identities; this gate does not authenticate their stored
bytes or prove that the baseline failed for the intended reason. Budget is checked
but not reserved, and no checkpoint or event is committed here.

The first supported diff vocabulary is deliberately narrow:

- UTF-8, LF-only Git unified diffs; at most 500,000 encoded bytes, 10,000 lines,
  25 distinct files and 5,000 added/deleted lines.
- Existing Python files under src/ or app/, also admitted by the repository grant.
  Tests, workflows, lock/config files and secret/reserved/traversal paths are denied.
- Matching unquoted diff/old/new paths, optional ordinary Git index header,
  explicit unified hunks with exact old/new line counts and non-overlapping ranges.
- No creation/deletion headers, renames, mode changes, binary formats, quoted
  filenames, CRLF, NUL bytes or no-final-newline markers. Unsupported formats fail
  closed. Each file must contain at least one added/deleted line.

The gate computes patch_ref from exact input UTF-8 bytes and derives changed
paths/line counts from hunks, never from model declarations. Its safe result
contains run/source/artifact bindings, hash, paths/counts, UTC timestamp and trace;
raw diff/source content is absent. Error codes contain no input text. Test names
use short explicit IDs for large adversarial bodies, avoiding raw diff bodies in
pytest telemetry and Windows environment limits.

This parser does not compare hunk context to source bytes or establish that a file
exists. It is not a replacement for git apply --check. A future trusted dispatcher
must retrieve and validate exact artifacts, recheck current consent/cancellation,
reserve budget atomically, materialize a fresh pinned source tree inside an
admitted remote sandbox, run fixed git apply --check and apply operations there,
and preserve source/test/patch/tree identities for verification. No arbitrary
shell or local repository execution is permitted. Runtime artifact storage,
auditable attempt deltas, application, regression execution and clean replay are
still pending. Existing synthetic M0 probe is not extended by this module.

All tests use synthetic text. Passing admission does not mean source changes are
safe, fix an incident, or have passed static/security/test gates. Python source
can contain arbitrary behavior; the runner and deterministic verifier remain the
security/evidence boundaries. M2 stays partial.
