"""Dependency provenance is a fingerprint, never dependency execution."""

import hashlib
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from packages.contracts.context import ContextBundle
from services.context.collector import ContextError, collect
from services.repository.snapshot import FileIdentity, Snapshot


def source(files: dict[str, bytes]) -> Snapshot:
    return Snapshot(
        "a" * 40,
        tuple(
            FileIdentity(p, "b" * 40, hashlib.sha256(b).hexdigest(), len(b))
            for p, b in files.items()
        ),
        MappingProxyType(files),
    )


def test_dependency_identity_independent_of_context_selection_and_order() -> None:
    files = {"requirements.txt": b"example==1.0\n", "src/a.py": b"x=1\n"}
    first = collect(source(files), ("src/a.py",), "")
    again = collect(source(dict(reversed(list(files.items())))), ("src/a.py",), "")
    assert first.deps_hash is not None and first.deps_hash == again.deps_hash
    assert first.dependency_files[0].path == "requirements.txt"
    assert (
        first.dependency_files[0].sha256
        == hashlib.sha256(files["requirements.txt"]).hexdigest()
    )
    changed = collect(
        source({**files, "requirements.txt": b"example==2.0\n"}), ("src/a.py",), ""
    )
    assert first.deps_hash != changed.deps_hash
    assert "example==1.0" not in first.model_dump_json()


def test_no_manifest_is_unknown_and_nested_files_are_not_discovered() -> None:
    bundle = collect(
        source({"src/requirements.txt": b"example==1.0"}), ("src/requirements.txt",), ""
    )
    assert bundle.deps_hash is None and bundle.dependency_files == ()


def test_dependency_hash_is_checked_even_when_not_selected_for_context() -> None:
    original = source({"requirements.txt": b"original", "src/a.py": b"x=1"})
    tampered = Snapshot(
        original.source_commit,
        original.files,
        MappingProxyType({"requirements.txt": b"changed", "src/a.py": b"x=1"}),
    )
    with pytest.raises(ContextError, match="SOURCE_INTEGRITY"):
        collect(tampered, ("src/a.py",), "")


def test_forged_aggregate_hash_is_rejected() -> None:
    bundle = collect(
        source({"requirements.txt": b"example==1.0"}), ("requirements.txt",), ""
    )
    with pytest.raises(ValidationError):
        ContextBundle.model_validate({**bundle.model_dump(), "deps_hash": "0" * 64})
