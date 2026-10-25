"""Deterministic bounded packaging of already admitted original source bytes.

No checkout, extraction, execution or network I/O. A manifest is metadata, not
execution consent: callers must enforce current source authorization separately.
"""

import hashlib
import io
import tarfile
from collections.abc import Mapping

from packages.contracts.sandbox import RunnerError
from packages.contracts.source import SourceManifest
from services.runner.workspace import archive_identity


def build_source_archive(manifest: SourceManifest, files: Mapping[str, bytes]) -> bytes:
    """Package the exact manifest subset, preserving original unsanitized bytes.

    Verify both provider Git blob IDs and SHA256 before packing. Deterministic
    directories and non-executable file modes produce a measurable upload body.
    This is a partial source subset unless the caller established completeness;
    it never proves the full commit tree, image digest or runner capabilities.
    """
    manifest = SourceManifest.model_validate(manifest.model_dump())
    if not manifest.files:
        raise RunnerError("S_SOURCE_ARCHIVE_EMPTY")
    if set(files) != {entry.path for entry in manifest.files}:
        raise RunnerError("S_SOURCE_ARCHIVE_MISMATCH")
    original: dict[str, bytes] = {}
    total = 0
    for entry in manifest.files:
        body = files[entry.path]
        if not isinstance(body, bytes):
            raise RunnerError("S_SOURCE_ARCHIVE_MISMATCH")
        total += len(body)
        if total > 65536:
            raise RunnerError("S_SOURCE_ARCHIVE_LIMIT")
        blob = hashlib.sha1(
            f"blob {len(body)}\0".encode() + body, usedforsecurity=False
        ).hexdigest()
        if (
            len(body) != entry.size_bytes
            or hashlib.sha256(body).hexdigest() != entry.sha256
            or blob != entry.blob_id
        ):
            raise RunnerError("S_SOURCE_ARCHIVE_MISMATCH")
        original[entry.path] = body
    directories = {
        "/".join(parts[:i])
        for path in original
        for parts in [path.split("/")]
        for i in range(1, len(parts))
    }
    # Bound member overhead before allocating the in-memory tar.
    if len(directories) + len(original) > 128:
        raise RunnerError("S_SOURCE_ARCHIVE_LIMIT")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as tree:
        for path in sorted(directories | original.keys()):
            item = tarfile.TarInfo(path)
            item.uid = item.gid = 1000
            try:
                item.tobuf(format=tarfile.USTAR_FORMAT)
            except (ValueError, UnicodeError):
                raise RunnerError("S_SOURCE_ARCHIVE_PATH") from None
            if path in directories and path not in original:
                item.type, item.mode = tarfile.DIRTYPE, 0o755
                tree.addfile(item)
            else:
                item.mode, item.size = 0o644, len(original[path])
                tree.addfile(item, io.BytesIO(original[path]))
    archive = output.getvalue()
    if len(archive) > 65536:
        raise RunnerError("S_SOURCE_ARCHIVE_LIMIT")
    archive_identity(archive)
    return archive
