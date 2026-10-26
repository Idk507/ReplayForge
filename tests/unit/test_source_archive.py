"""Tiny source archives: no execution, filesystem traversal or provider calls."""

import hashlib
import io
import tarfile

import pytest

from packages.contracts.sandbox import RunnerError
from packages.contracts.source import SourceFile
from services.runner.source_archive import build_source_archive
from services.runner.workspace import archive_identity
from tests.unit.test_source_manifest import manifest


def source(path: str, content: bytes) -> SourceFile:
    return SourceFile(
        path=path,
        blob_id=hashlib.sha1(
            f"blob {len(content)}\0".encode() + content, usedforsecurity=False
        ).hexdigest(),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def test_archive_preserves_original_bytes_and_has_stable_identity():
    files = {"src/a.py": b"x = 1\n", "tests/test_a.py": b"# inert\n"}
    proof = manifest(files=tuple(source(p, b) for p, b in files.items()))
    body = build_source_archive(proof, files)
    assert body == build_source_archive(proof, dict(reversed(list(files.items()))))
    assert archive_identity(body)[1:] == (2, 14)
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as tree:
        item = tree.extractfile("src/a.py")
        assert item is not None and item.read() == files["src/a.py"]
        assert all(m.uid == 1000 and m.gid == 1000 and m.mtime == 0 for m in tree)


@pytest.mark.parametrize("files", [{}, {"src/a.py": b"changed"}, {"extra.py": b"x"}])
def test_archive_requires_exact_original_manifest_content(files):
    proof = manifest(files=(source("src/a.py", b"original"),))
    with pytest.raises(RunnerError, match="S_SOURCE_ARCHIVE"):
        build_source_archive(proof, files)


@pytest.mark.parametrize(
    "field,value", [("blob_id", "f" * 40), ("sha256", "f" * 64), ("size_bytes", 5)]
)
def test_archive_checks_both_content_hashes_and_size(field, value):
    entry = source("src/a.py", b"x").model_copy(update={field: value})
    with pytest.raises(RunnerError, match="S_SOURCE_ARCHIVE"):
        build_source_archive(manifest(files=(entry,)), {"src/a.py": b"x"})


def test_archive_rejects_file_ancestor_collision():
    files = {"src": b"x", "src/a.py": b"y"}
    with pytest.raises(RunnerError, match="S_WORKSPACE_INVALID"):
        build_source_archive(
            manifest(files=tuple(source(p, b) for p, b in files.items())), files
        )


def test_archive_bounds_tar_overhead_not_only_file_bytes():
    files = {"src/a.py": b"x" * 65536}
    with pytest.raises(RunnerError, match="S_SOURCE_ARCHIVE_LIMIT"):
        build_source_archive(
            manifest(files=(source("src/a.py", files["src/a.py"]),)), files
        )


def test_archive_rejects_empty_manifest():
    with pytest.raises(RunnerError, match="S_SOURCE_ARCHIVE_EMPTY"):
        build_source_archive(manifest(), {})


def test_archive_reports_unrepresentable_tar_path_without_raw_exception():
    path = "a" * 150 + ".py"
    with pytest.raises(RunnerError, match="S_SOURCE_ARCHIVE_PATH"):
        build_source_archive(manifest(files=(source(path, b"x"),)), {path: b"x"})
