"""Bounded Git SHA-1 object validation without filesystem materialization."""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol
from uuid import UUID

from packages.contracts.repository import RepositoryGrant
from packages.policies.repository import (
    admit,
    allowed_entry,
    portable_tree_path,
    safe_tree_path,
)


class SnapshotError(Exception):
    """Safe stable code; raw source and transport exceptions stay private."""


class ObjectSource(Protocol):
    """Trusted bounded transport must enforce timeout/bytes before allocation."""

    def read_object(self, oid: str, max_bytes: int) -> tuple[str, bytes]: ...


@dataclass(frozen=True)
class FileIdentity:
    """Source identity plus SHA256 evidence for one allowed file."""

    path: str
    blob_id: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Snapshot:
    """Read-only approved subset; content is untrusted and never executable."""

    source_commit: str
    files: tuple[FileIdentity, ...]
    _content: Mapping[str, bytes] = field(repr=False)
    commit_identity: Literal["GIT_OBJECT_HASH", "GITHUB_HTTPS_METADATA"] = (
        "GIT_OBJECT_HASH"
    )
    evidence_origin: Literal["NOT_RECORDED", "TEST_TRANSPORT", "REMOTE_SERVICE"] = (
        "NOT_RECORDED"
    )

    def read_file(self, path: str, max_bytes: int) -> bytes:
        """Return exact approved bytes; never normalize a requested path."""
        if path not in self._content:
            raise SnapshotError("PATH_DENIED")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 65536:
            raise SnapshotError("READ_LIMIT")
        body = self._content[path]
        if len(body) > max_bytes:
            raise SnapshotError("READ_LIMIT")
        return body


def load_snapshot(
    source: ObjectSource, grant: RepositoryGrant, caller: UUID, commit: str
) -> Snapshot:
    """Verify an admitted commit and all traversed tree objects, then allowed blobs.

    SHA-1 here checks Git object identity, not author authenticity or collision
    resistance. Independently authenticated source refs are still required.
    """
    return _load_snapshot(source, grant, caller, commit)


def load_attested_tree(
    source: ObjectSource,
    grant: RepositoryGrant,
    caller: UUID,
    commit: str,
    tree: str,
    *,
    selected_paths: tuple[str, ...] | None = None,
) -> Snapshot:
    """Trusted adapter only: commit/tree binding comes from authenticated provider.

    Every tree/blob still undergoes raw hash/path checks. This distinct entry
    point never claims local raw-commit verification and is not a model tool.
    """
    if not re.fullmatch(r"[a-f0-9]{40}", tree):
        raise SnapshotError("TREE_IDENTITY")
    return _load_snapshot(
        source, grant, caller, commit, root_tree=tree, selected_paths=selected_paths
    )


def _load_snapshot(
    source: ObjectSource,
    grant: RepositoryGrant,
    caller: UUID,
    commit: str,
    *,
    root_tree: str | None = None,
    selected_paths: tuple[str, ...] | None = None,
) -> Snapshot:
    """Shared bounded tree traversal; raw and provider commit proofs stay distinct."""
    grant = RepositoryGrant.model_validate(grant.model_dump())
    if not admit(grant, caller, grant.repository_id, commit).allowed:
        raise SnapshotError("SCOPE_DENIED")
    if selected_paths is not None and (
        not 1 <= len(selected_paths) <= 16
        or len(set(selected_paths)) != len(selected_paths)
        or any(not allowed_entry(grant, path, "100644") for path in selected_paths)
    ):
        raise SnapshotError("SELECTED_PATH_DENIED")
    traversal = grant.approved_paths if selected_paths is None else selected_paths
    calls = 0
    total = 0
    paths: set[str] = set()
    content: dict[str, bytes] = {}
    identities: list[FileIdentity] = []

    def fetch(oid: str, expected: str) -> bytes:
        nonlocal calls, total
        if calls >= 128 or total >= 2097152:
            raise SnapshotError("SNAPSHOT_BUDGET")
        calls += 1
        try:
            kind, body = source.read_object(oid, min(65536, 2097152 - total))
        except (OSError, KeyError, TimeoutError):
            raise SnapshotError("OBJECT_UNAVAILABLE") from None
        if kind != expected or not isinstance(body, bytes) or len(body) > 65536:
            raise SnapshotError("OBJECT_SCHEMA")
        total += len(body)
        if total > 2097152:
            raise SnapshotError("SNAPSHOT_BUDGET")
        digest = hashlib.sha1(f"{kind} {len(body)}\0".encode() + body).hexdigest()
        if digest != oid:
            raise SnapshotError("OBJECT_IDENTITY")
        return body

    if root_tree is None:
        header = fetch(commit, "commit").split(b"\n", 1)[0]
        if not re.fullmatch(rb"tree [a-f0-9]{40}", header):
            raise SnapshotError("COMMIT_SCHEMA")
        root_tree = header[5:].decode("ascii")
        identity: Literal["GIT_OBJECT_HASH", "GITHUB_HTTPS_METADATA"] = (
            "GIT_OBJECT_HASH"
        )
    else:
        identity = "GITHUB_HTTPS_METADATA"

    def walk(oid: str, prefix: str, depth: int) -> None:
        if depth > 16:
            raise SnapshotError("TREE_DEPTH")
        tree = fetch(oid, "tree")
        offset = 0
        while offset < len(tree):
            end = tree.find(b"\0", offset)
            if end < 0 or end + 21 > len(tree):
                raise SnapshotError("TREE_SCHEMA")
            raw = tree[offset:end].split(b" ", 1)
            if len(raw) != 2:
                raise SnapshotError("TREE_SCHEMA")
            try:
                mode, name = (part.decode("ascii") for part in raw)
            except UnicodeDecodeError:
                raise SnapshotError("PATH_DENIED") from None
            child = tree[end + 1 : end + 21].hex()
            offset = end + 21
            if not name or "/" in name or name in {".", ".."}:
                raise SnapshotError("PATH_DENIED")
            path = prefix + name
            folded = path.lower()
            if folded in paths:
                raise SnapshotError("PATH_COLLISION")
            paths.add(folded)
            if len(paths) > 512:
                raise SnapshotError("TREE_LIMIT")
            if mode == "40000":
                relevant = any(
                    rule.startswith(path + "/") or path.startswith(rule)
                    for rule in traversal
                )
                if relevant:
                    if not safe_tree_path(path):
                        raise SnapshotError("PATH_DENIED")
                    walk(child, path + "/", depth + 1)
            elif mode not in {"100644", "100755"}:
                raise SnapshotError("ENTRY_MODE_DENIED")
            elif allowed_entry(grant, path, mode) and (
                selected_paths is None or path in selected_paths
            ):
                body = fetch(child, "blob")
                try:
                    body.decode("utf-8")
                except UnicodeDecodeError:
                    raise SnapshotError("BINARY_DENIED") from None
                if b"\0" in body or body.startswith(
                    b"version https://git-lfs.github.com/spec/v1"
                ):
                    raise SnapshotError("BINARY_OR_LFS_DENIED")
                content[path] = body
                identities.append(
                    FileIdentity(
                        path, child, hashlib.sha256(body).hexdigest(), len(body)
                    )
                )
            else:
                # Denied paths are omitted; unsafe names must still fail closed.
                if not portable_tree_path(path):
                    raise SnapshotError("PATH_DENIED")

    walk(root_tree, "", 0)
    if selected_paths is not None and set(content) != set(selected_paths):
        raise SnapshotError("SELECTED_PATH_MISSING")
    return Snapshot(
        commit,
        tuple(sorted(identities, key=lambda item: item.path)),
        MappingProxyType(content),
        identity,
    )
