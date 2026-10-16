# Bounded baseline report inspection

services.runner.baseline.inspect_baseline qualifies JUnit data for the planned
regression target. It does not execute tests, read files, authenticate a runner,
persist artifacts, mutate checkpoints or authorize PATCHING. Every result says
evidence_origin=UNAUTHENTICATED_INPUT and can_authorize_patching=false. Even a
MATCHED_ASSERTION is only a structural/content match in supplied XML.

Inputs are an exact TestCommandPlan, report bytes, expected content-addressed
report reference, process exit code and validated FailureExpectation. The plan
must be BASELINE and the report hash must match the supplied reference. Only
pytest exit code 1 is eligible. The expected target is the module-level test
function with classname derived from reproduction_path. Parameterized/class test
identities are intentionally unsupported in this first slice.

Qualification requires exactly one failed case, that case being the expected
regression, an AssertionError and an exact bounded uppercase behavioral marker
in its failure message/text. Other cases may pass. Any error/skip, another failure,
wrong target, missing marker, duplicate identity or inconsistent declared counts
prevents a match. Failure text containing syntax/import/collection errors also
fails qualification. This does not establish that a marker or assertion actually
represents the incident's business behavior; independent evidence review remains
necessary. A deliberately fabricated report can structurally match and still
cannot authorize patching.

XML controls: maximum 128 KiB, strict UTF-8 (optional UTF-8 BOM), no NUL, DTD or
entity declarations, no external resolution or filesystem access. Flat pytest
testsuite/testsuites collections are supported; at most 16 suites, 256 cases,
2048 XML nodes and depth 64. Outcomes contain text only, metadata has bounded
structural positions, and supplied suite/root counters must agree with parsed
cases. Unsupported/nested structures fail closed. Parser errors return safe
reason codes without XML snippets, failure messages, markers or output logs.

Results retain only safe run/source/reproduction bindings, report hash, status,
reason, UTC observation timestamp and local trace. They do not assert a remote
observation time, signature, image identity, source tree, termination, timeout
status or execution ID. Those must come from an independently authenticated and
correlated runner receipt. JUnit is untrusted workload output; its checksum proves
content identity, not origin or truth. Raw reports are never saved by this module.

Explicit signature/correlation verification and report binding now exist in
runner-receipts.md. No production signer or live receipt is configured.
Next integration must retrieve/check retained artifacts, provision runner identity,
bind execution to source/image/test/command identities, reject timeout/signal/setup
failures, reserve budgets and commit evidence through the graph reducer under a
live lease. No MATCHED_ASSERTION-to-PATCHING path is added here. Genuine failing
baseline, candidate regression and clean replay acceptance remain pending.
