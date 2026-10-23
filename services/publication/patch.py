"""Strict pure text patch application; no filesystem, shell or code execution."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha1

from packages.contracts.repository import RepositoryGrant
from packages.policies.repository import allowed_entry
from services.runner.patches import PatchDenied, _paths


class PublicationDenied(Exception):
    """Stable bounded failure, without provider bodies, source or credentials."""


@dataclass(frozen=True)
class FileEdit:
    path: str
    before_sha: str
    content: str = field(repr=False)

    @property
    def after_sha(self) -> str:
        return blob_sha(self.content.encode())


def blob_sha(content: bytes) -> str:
    """Git object identity, not a SHA256 artifact or signature."""
    return sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def patch_paths(diff: str, grant: RepositoryGrant) -> tuple[str, ...]:
    try:
        paths, _ = _paths(diff)
    except PatchDenied:
        raise PublicationDenied("PUBLICATION_PATCH_INVALID") from None
    if any(
        not p.startswith(("src/", "app/"))
        or not p.endswith(".py")
        or not allowed_entry(grant, p, "100644")
        for p in paths
    ):
        raise PublicationDenied("PUBLICATION_PATCH_SCOPE")
    return paths


def apply_approved_patch(
    diff: str,
    originals: Mapping[str, bytes],
    grant: RepositoryGrant,
) -> tuple[FileEdit, ...]:
    """Apply only exact hunks, preserving all other bytes; no fuzzy matching."""
    paths = patch_paths(diff, grant)
    if set(originals) != set(paths):
        raise PublicationDenied("PUBLICATION_SOURCE_MISSING")
    lines = diff.split("\n")[:-1]
    edits = []
    i = 0
    for path in paths:
        data = originals[path]
        if (
            len(data) > 65536
            or b"\0" in data
            or b"\r" in data
            or (data and not data.endswith(b"\n"))
        ):
            raise PublicationDenied("PUBLICATION_SOURCE_INVALID")
        try:
            old = [line + "\n" for line in data.decode("utf-8").split("\n")[:-1]]
        except UnicodeError:
            raise PublicationDenied("PUBLICATION_SOURCE_INVALID") from None
        i += 1  # _paths has already validated exact headers, counts and ordering.
        if lines[i].startswith("index "):
            i += 1
        i += 2
        out: list[str] = []
        cursor = 0
        while i < len(lines) and lines[i].startswith("@@ "):
            match = re.fullmatch(
                r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?", lines[i]
            )
            if match is None:
                raise PublicationDenied("PUBLICATION_PATCH_INVALID")
            start, count = int(match[1]), int(match[2] or "1")
            new_start, new_count = int(match[3]), int(match[4] or "1")
            position = start - 1 if count else start
            if not cursor <= position <= len(old):
                raise PublicationDenied("PUBLICATION_CONTEXT_MISMATCH")
            out.extend(old[cursor:position])
            cursor = position
            if len(out) != (new_start - 1 if new_count else new_start):
                raise PublicationDenied("PUBLICATION_CONTEXT_MISMATCH")
            i += 1
            consumed = added = 0
            while consumed < count or added < new_count:
                line = lines[i]
                text = line[1:] + "\n"
                if line[0] != "+":
                    if cursor >= len(old) or old[cursor] != text:
                        raise PublicationDenied("PUBLICATION_CONTEXT_MISMATCH")
                    cursor += 1
                    consumed += 1
                if line[0] != "-":
                    out.append(text)
                    added += 1
                i += 1
        out.extend(old[cursor:])
        content = "".join(out)
        if len(content.encode()) > 65536:
            raise PublicationDenied("PUBLICATION_SOURCE_INVALID")
        edits.append(FileEdit(path, blob_sha(data), content))
    return tuple(edits)
