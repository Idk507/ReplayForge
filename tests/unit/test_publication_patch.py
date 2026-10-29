"""Tiny inert text application; repository code is never executed."""

from hashlib import sha1
from uuid import uuid4

import pytest

from packages.contracts.repository import RepositoryGrant
from services.publication.patch import (
    PublicationDenied,
    apply_approved_patch,
    blob_sha,
)

DIFF = (
    "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    "@@ -1,2 +1,2 @@\n x=1\n-y=2\n+y=3\n"
)


def grant():
    return RepositoryGrant(
        repository_id=uuid4(),
        owner_id=uuid4(),
        remote_url="https://github.com/example/demo",
        commits=("a" * 40,),
        approved_paths=("src/",),
        consent=True,
    )


def test_apply_hashes_exact_unchanged_context_without_filesystem():
    edits = apply_approved_patch(DIFF, {"src/a.py": b"x=1\ny=2\n"}, grant())
    assert len(edits) == 1 and edits[0].content == "x=1\ny=3\n"
    assert edits[0].before_sha == sha1(b"blob 8\0x=1\ny=2\n").hexdigest()
    assert "y=3" not in repr(edits[0])


@pytest.mark.parametrize(
    "fault",
    [
        "context",
        "path",
        "workflow",
        "missing",
        "binary",
        "position",
        "new_position",
        "oversized",
    ],
)
def test_patch_application_rejects_unapproved_or_inexact_edits(fault):
    diff = DIFF
    files = {"src/a.py": b"x=1\ny=2\n"}
    if fault == "context":
        files["src/a.py"] = b"x=9\ny=2\n"
    if fault == "path":
        diff = diff.replace("src/a.py", "tests/a.py")
    if fault == "workflow":
        diff = diff.replace("src/a.py", ".github/workflows/a.py")
    if fault == "missing":
        files = {}
    if fault == "binary":
        files["src/a.py"] = b"\0"
    if fault == "position":
        diff = diff.replace("-1,2", "-2,2")
    if fault == "new_position":
        diff = diff.replace("+1,2", "+2,2")
    if fault == "oversized":
        files["src/a.py"] = b"x" * 65537
    with pytest.raises(PublicationDenied):
        apply_approved_patch(diff, files, grant())


def test_zero_count_insertion_and_multiple_hunks_preserve_other_lines():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -0,0 +1 @@\n+start\n@@ -2 +3 @@\n-b\n+end\n"
    )
    assert (
        apply_approved_patch(diff, {"src/a.py": b"a\nb\n"}, grant())[0].content
        == "start\na\nend\n"
    )


def test_checkout_path_publication_policy_matches_patch_admission():
    policy = grant().model_copy(update={"approved_paths": ("checkout/",)})
    diff = DIFF.replace("src/a.py", "checkout/payments.py")
    edit = apply_approved_patch(diff, {"checkout/payments.py": b"x=1\ny=2\n"}, policy)
    assert edit[0].path == "checkout/payments.py"
    for denied in ("checkout/tests/test_a.py", "checkout-other/a.py"):
        with pytest.raises(PublicationDenied):
            apply_approved_patch(
                diff.replace("checkout/payments.py", denied),
                {denied: b"x=1\ny=2\n"},
                policy,
            )


def test_publication_rejects_candidate_that_edits_regression_test():
    malicious = DIFF.replace("src/a.py", "checkout/tests/test_payments.py")
    with pytest.raises(PublicationDenied, match="PUBLICATION_PATCH_SCOPE"):
        apply_approved_patch(
            malicious,
            {"checkout/tests/test_payments.py": b"assert False\n"},
            grant().model_copy(update={"approved_paths": ("checkout/",)}),
        )


def test_unicode_line_separator_inside_source_is_not_a_git_hunk_boundary():
    original = "value='a\u2028b'\n".encode()
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n-value='a\u2028b'\n+value='c\u2028d'\n"
    )
    assert (
        apply_approved_patch(diff, {"src/a.py": original}, grant())[0].content
        == "value='c\u2028d'\n"
    )


def test_missing_final_newline_replacement_matches_runner_patch_semantics():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n-x=1\n\\ No newline at end of file\n+x=2\n"
    )
    edit = apply_approved_patch(diff, {"src/a.py": b"x=1"}, grant())[0]
    assert edit.content == "x=2\n"
    assert edit.before_sha == blob_sha(b"x=1")
    assert edit.after_sha == blob_sha(b"x=2\n")


def test_missing_final_newline_outside_changed_hunk_is_preserved():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n-x=1\n+x=2\n"
    )
    edit = apply_approved_patch(diff, {"src/a.py": b"x=1\ny=2"}, grant())[0]
    assert edit.content == "x=2\ny=2"


@pytest.mark.parametrize("source", [b"x=1\r\ny=2\n", b"x=1\0y=2\n"])
def test_publication_still_rejects_crlf_and_nul_source(source):
    with pytest.raises(PublicationDenied, match="PUBLICATION_SOURCE_INVALID"):
        apply_approved_patch(DIFF, {"src/a.py": source}, grant())
