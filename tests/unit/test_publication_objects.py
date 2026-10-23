"""Fixed object vectors through Git's own hash plumbing; no workload execution."""

import subprocess
from datetime import UTC, datetime

import pytest

from services.publication.objects import (
    TreeEntry,
    commit_bytes,
    git_sha,
    tree_bytes,
    tree_sha,
)
from services.publication.patch import PublicationDenied


def test_tree_and_commit_identities_match_git_hash_object():
    entries = [
        TreeEntry(path="z.py", mode="100644", type="blob", sha="a" * 40),
        TreeEntry(path="src", mode="040000", type="tree", sha="b" * 40),
    ]
    raw = tree_bytes(entries)
    result = subprocess.run(
        ["git", "hash-object", "-t", "tree", "--stdin"],
        input=raw,
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.decode().strip() == tree_sha(entries)
    commit = commit_bytes(
        tree_sha(entries),
        "c" * 40,
        "ReplayForge test\n",
        datetime(2026, 10, 22, tzinfo=UTC),
    )
    result = subprocess.run(
        ["git", "hash-object", "-t", "commit", "--stdin"],
        input=commit,
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.decode().strip() == git_sha("commit", commit)


@pytest.mark.parametrize("paths", [("a", "a"), ("bad/name",), ("a\0b",)])
def test_non_tree_entry_names_are_rejected(paths):
    with pytest.raises(PublicationDenied):
        tree_bytes(
            [TreeEntry(path=p, mode="100644", type="blob", sha="a" * 40) for p in paths]
        )
