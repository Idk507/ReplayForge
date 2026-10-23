"""Git SHA-1 object encoding for verified trees and reproducible commit writes."""

from datetime import UTC, datetime
from hashlib import sha1
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from packages.contracts.run import CommitSHA
from services.publication.patch import PublicationDenied

AUTHOR = "ReplayForge <replayforge@users.noreply.github.com>"


class TreeEntry(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    mode: Literal["040000", "100644", "100755", "120000", "160000"]
    type: Literal["blob", "tree", "commit"]
    sha: CommitSHA

    @model_validator(mode="after")
    def kind(self) -> "TreeEntry":
        expected = (
            "tree"
            if self.mode == "040000"
            else "commit"
            if self.mode == "160000"
            else "blob"
        )
        if self.type != expected:
            raise ValueError("GIT_ENTRY_INVALID")
        return self


def git_sha(kind: Literal["tree", "commit"], raw: bytes) -> str:
    return sha1(f"{kind} {len(raw)}\0".encode() + raw).hexdigest()


def tree_bytes(entries: list[TreeEntry]) -> bytes:
    names = [e.path for e in entries]
    if (
        len(entries) > 4096
        or len(set(names)) != len(names)
        or any(p in {".", ".."} or "/" in p or "\0" in p for p in names)
    ):
        raise PublicationDenied("PUBLICATION_TREE_INVALID")
    try:
        ordered = sorted(
            entries, key=lambda e: (e.path + ("/" if e.type == "tree" else "")).encode()
        )
        return b"".join(
            e.mode.lstrip("0").encode()
            + b" "
            + e.path.encode()
            + b"\0"
            + bytes.fromhex(e.sha)
            for e in ordered
        )
    except (UnicodeError, ValueError):
        raise PublicationDenied("PUBLICATION_TREE_INVALID") from None


def tree_sha(entries: list[TreeEntry]) -> str:
    return git_sha("tree", tree_bytes(entries))


def commit_bytes(tree: str, parent: str, message: str, issued_at: datetime) -> bytes:
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise PublicationDenied("PUBLICATION_CLOCK_INVALID")
    timestamp = int(issued_at.astimezone(UTC).timestamp())
    return (
        f"tree {tree}\nparent {parent}\nauthor {AUTHOR} {timestamp} +0000\n"
        f"committer {AUTHOR} {timestamp} +0000\n\n{message}"
    ).encode()
