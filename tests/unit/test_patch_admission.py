"""Patch admission is inert and never runs git or repository code."""

from uuid import uuid4

import pytest

from packages.contracts.run import Stage
from services.runner.patches import PatchDenied, admit_patch
from tests.unit.test_command_policy import inputs

DIFF = (
    "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    "@@ -1 +1 @@\n-x=1\n+x=2\n"
)


def context():
    run, grant, owner, _ = inputs()
    run = run.model_copy(
        update={
            "current_stage": Stage.PATCHING,
            "baseline_result_ref": "sha256:" + "e" * 64,
        }
    )
    grant = grant.model_copy(update={"approved_paths": ("src/", "app/", "tests/")})
    return run, grant, owner


def call(diff=DIFF, **overrides):
    run, grant, owner = context()
    return admit_patch(
        diff,
        run,
        grant,
        owner,
        source_commit=overrides.get("source_commit", run.source_commit),
        source_manifest_ref=run.source_manifest_ref,
        baseline_result_ref=run.baseline_result_ref,
        reproduction_test_ref=run.reproduction_test_ref,
    )


def test_valid_diff_has_actual_hash_and_no_execution_permission() -> None:
    import hashlib

    result = call()
    assert result.files_changed == ("src/a.py",) and result.changed_lines == 2
    assert result.patch_ref == "sha256:" + hashlib.sha256(DIFF.encode()).hexdigest()
    assert result.can_authorize_execution is False
    assert "x=2" not in result.model_dump_json()


@pytest.mark.parametrize(
    "path",
    [
        "../a.py",
        "src/../a.py",
        ".github/workflows/a.yml",
        "tests/test_regression.py",
        "uv.lock",
        "src/a.key",
        "src/con.py",
    ],
)
def test_forbidden_paths_rejected(path) -> None:
    with pytest.raises(PatchDenied):
        call(DIFF.replace("src/a.py", path))


@pytest.mark.parametrize(
    "diff",
    [
        DIFF.replace("+x=2\n", ""),
        DIFF.replace("@@ -1 +1 @@", "@@ -1,2 +1 @@"),
        DIFF.replace("+++ b/src/a.py", "+++ b/src/b.py"),
        DIFF.replace("--- a/src/a.py", "--- /dev/null"),
        DIFF + DIFF,
        DIFF + "GIT binary patch\n",
        DIFF.replace("--- a/", "old mode 100644\nnew mode 100755\n--- a/"),
        DIFF.replace("\n", "\r\n"),
        DIFF + "\0",
        "x" * 500001,
    ],
    ids=[
        "truncated",
        "counts",
        "rename",
        "create",
        "duplicate",
        "binary",
        "mode",
        "crlf",
        "nul",
        "size",
    ],
)
def test_malformed_ambiguous_binary_mode_and_oversized_diffs_rejected(diff) -> None:
    with pytest.raises(PatchDenied):
        call(diff)


def test_wrong_source_and_unconsented_owner_rejected() -> None:
    with pytest.raises(PatchDenied):
        call(source_commit="f" * 40)
    (
        run,
        grant,
        _,
    ) = context()
    with pytest.raises(PatchDenied):
        admit_patch(
            DIFF,
            run,
            grant,
            uuid4(),
            source_commit=run.source_commit,
            source_manifest_ref=run.source_manifest_ref,
            baseline_result_ref=run.baseline_result_ref,
            reproduction_test_ref=run.reproduction_test_ref,
        )


def test_multiple_files_and_index_header_are_admitted() -> None:
    first = DIFF.replace("--- a/", "index aaaaaaa..bbbbbbb 100644\n--- a/")
    result = call(first + DIFF.replace("src/a.py", "app/b.py"))
    assert result.files_changed == ("src/a.py", "app/b.py")
    assert result.changed_lines == 4


@pytest.mark.parametrize(
    "case", ["file_limit", "line_limit", "unicode_bytes", "overlap"]
)
def test_limits_and_overlapping_hunks_rejected(case) -> None:
    if case == "file_limit":
        diff = "".join(DIFF.replace("src/a.py", f"src/a{i}.py") for i in range(26))
    elif case == "line_limit":
        diff = DIFF[: DIFF.index("@@")] + "@@ -1 +1,5001 @@\n-x=1\n" + "+x=2\n" * 5001
    elif case == "unicode_bytes":
        diff = DIFF.replace("+x=2", "+" + "é" * 250000)
    else:
        diff = DIFF + "@@ -1 +1 @@\n-x=1\n+x=3\n"
    with pytest.raises(PatchDenied):
        call(diff)


@pytest.mark.parametrize(
    "change", ["stage", "cancel", "budget", "baseline", "reproduction", "manifest"]
)
def test_checkpoint_gates_cannot_be_waived(change) -> None:
    run, grant, owner = context()
    original = run
    updates = {
        "stage": {"current_stage": Stage.TRIAGING},
        "cancel": {"cancellation_requested": True},
        "budget": {
            "remaining_budget": run.remaining_budget.model_copy(
                update={"patch_attempts": 0}
            )
        },
        "baseline": {"baseline_result_ref": None},
        "reproduction": {"reproduction_test_ref": None},
        "manifest": {"source_manifest_ref": None},
    }
    run = run.model_copy(update=updates[change])
    with pytest.raises(PatchDenied):
        admit_patch(
            DIFF,
            run,
            grant,
            owner,
            source_commit=original.source_commit,
            source_manifest_ref=original.source_manifest_ref,
            baseline_result_ref=original.baseline_result_ref,
            reproduction_test_ref=original.reproduction_test_ref,
        )
