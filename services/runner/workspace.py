"""Bounded saved-image workspace measurement, without extraction or execution."""

import hashlib
import io
import json
import re
import tarfile
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.run import ArtifactID
from packages.contracts.sandbox import RunnerError
from services.runner.remote import NebiusSandboxBackend, SandboxConfig


class WorkspaceMeasurement(BaseModel):
    """Content identity only; not image attestation or execution authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    image_uuid: UUID
    tree_ref: ArtifactID
    files: int = Field(strict=True, ge=1, le=128)
    bytes_measured: int = Field(strict=True, ge=0, le=65536)
    evidence_origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    can_authorize_verification: Literal[False] = False


def archive_identity(archive: bytes) -> tuple[str, int, int]:
    """Hash every regular file and directory; reject aliases and special entries.

    Input is an uncompressed POSIX tar rooted at the requested directory. Never
    extract guest-controlled paths. Bound both wire size and expanded contents.
    Ownership and timestamps are excluded; permission bits remain significant.
    """
    if not archive or len(archive) > 65536:
        raise RunnerError("S_WORKSPACE_LIMIT")
    entries: dict[str, tuple[str, int, str]] = {}
    total = files = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tree:
            for entry in tree:
                name = entry.name
                if name in {".", "./"} and entry.isdir():
                    continue
                if name.startswith("./"):
                    name = name[2:]
                name = name.removesuffix("/") if entry.isdir() else name
                if (
                    len(entries) >= 128
                    or len(name) > 240
                    or not re.fullmatch(r"[A-Za-z0-9_. /-]+", name)
                    or any(p in {"", ".", ".."} for p in name.split("/"))
                    or name.startswith("/")
                    or name.casefold() in entries
                    or not (entry.isfile() or entry.isdir())
                    or entry.mode & ~0o777
                    or entry.size < 0
                    or entry.size > 65536
                    or total + entry.size > 65536
                    or entry.issparse()
                ):
                    raise RunnerError("S_WORKSPACE_INVALID")
                digest = ""
                if entry.isfile():
                    stream = tree.extractfile(entry)
                    if stream is None:
                        raise RunnerError("S_WORKSPACE_INVALID")
                    with stream:
                        content = stream.read(65537)
                    if len(content) != entry.size:
                        raise RunnerError("S_WORKSPACE_INVALID")
                    digest = hashlib.sha256(content).hexdigest()
                    total += len(content)
                    files += 1
                elif entry.size:
                    raise RunnerError("S_WORKSPACE_INVALID")
                entries[name.casefold()] = (name, entry.mode, digest)
        if not files:
            raise RunnerError("S_WORKSPACE_EMPTY")
        # A file cannot also be an ancestor directory, regardless of tar ordering.
        file_paths = {name.casefold() for name, _, digest in entries.values() if digest}
        for name, _, _ in entries.values():
            parts = name.casefold().split("/")
            if any("/".join(parts[:i]) in file_paths for i in range(1, len(parts))):
                raise RunnerError("S_WORKSPACE_INVALID")
        body = json.dumps(
            {"schema": "replayforge.workspace.v1", "entries": sorted(entries.values())},
            separators=(",", ":"),
        ).encode()
        return "sha256:" + hashlib.sha256(body).hexdigest(), files, total
    except (tarfile.TarError, OSError, ValueError, UnicodeError):
        raise RunnerError("S_WORKSPACE_INVALID") from None


def measure_workspace(
    config: SandboxConfig,
    image_uuid: UUID,
    *,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> WorkspaceMeasurement:
    """One bounded GET of /workspace/repo in a caller-admitted immutable image.

    The trusted controller must resolve image ownership before calling. This
    primitive cannot grant repository consent or sign a runner receipt.
    """
    if not isinstance(image_uuid, UUID) or (transport is None and not allow_live):
        raise RunnerError("S_POLICY_DENIED")
    backend = NebiusSandboxBackend(config, transport=transport)
    try:
        archive = backend._request(
            "GET", f"v1/inspect/{image_uuid}/archive?path=/workspace/repo", 200
        )
        digest, files, size = archive_identity(archive)
        return WorkspaceMeasurement(
            image_uuid=image_uuid,
            tree_ref=digest,
            files=files,
            bytes_measured=size,
            evidence_origin="TEST_TRANSPORT"
            if transport is not None
            else "REMOTE_SERVICE",
        )
    finally:
        backend.close()
