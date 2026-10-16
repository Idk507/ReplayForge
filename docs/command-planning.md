# Inert pytest command planning

TestCommandRequest and plan_pytest provide the first M2 command broker slice.
They do not execute a process, read a filesystem, contact a provider, reserve a
budget or advance a run. No execution endpoint is added to SandboxBackend.
Plans explicitly have can_authorize_execution=false and status PLANNED.

The request binds run/repository UUIDs, immutable source commit, source manifest
reference, reproduction artifact reference, declared image SHA256, phase and
1–16 test file paths. The regression path must be included. Paths must be safe
portable Python files under tests/, with no case collisions or duplicates.
Node selectors, flags, shell strings, executable paths, timeouts and environment
values are not request fields; unknown fields fail schema validation.

The broker revalidates input and current checkpoint, then checks owner consent,
exact source/artifact/run bindings, approved paths, cancellation and remaining
sandbox-command budget. BASELINE requires REPRODUCING with no patch reference.
CANDIDATE/CLEAN_REPLAY require VERIFYING, a baseline result reference and the
latest patch-attempt reference. These references are bindings, not proof of
artifact existence or a valid baseline; deterministic evidence checks remain
mandatory before dispatch or any verification verdict.

The only template is PYTEST_FILES_V1:

`/usr/local/bin/python -I -m pytest -c /dev/null -o addopts= -q -p no:cacheprovider --import-mode=importlib --junitxml=/tmp/replayforge-result.xml -- <validated test files>`

It uses shell=false, fixed cwd /workspace/repo, 60-second timeout, no inherited
environment and only PYTEST_DISABLE_PLUGIN_AUTOLOAD=1. Repository pytest config
and ambient addopts cannot change the template. Plan schema validation rejects
modified argv, environment and execution-authority flags. Test ordering is the
explicit request order. This selects files, not proof of complete suite coverage.

Security limits: pytest still executes untrusted test modules and conftest code.
Plugin auto-loading controls do not make repository code safe. A future runner
must materialize validated source/test/patch bytes, reject links and path escapes,
bind and verify the actual OCI image, enforce required resource/network/filesystem
controls, reserve budget atomically, recheck cancellation and capture correlated
results/artifacts. The declared image hash alone is not verified identity. A
planning-time budget check is not an atomic reservation. No permission to run
repository code on the API or Windows host is introduced.

No result means no claim that reproduction passed or failed. Bounded unauthenticated
JUnit inspection and skip/count checks now exist (baseline-inspection.md); this
does not establish runner origin or authorize patching. Authenticated baseline
failure specificity, remote patch application,
clean replay, stage scheduling and signed evidence remain pending. Existing M0
remote smoke remains synthetic-only; production runner admission stays closed.
