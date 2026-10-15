"""Bounded sanitized context; construction does not authenticate source provenance."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.contracts.run import CommitSHA

DEPENDENCY_PATHS = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "requirements.txt",
        "requirements-dev.txt",
    }
)


class DependencyFile(BaseModel):
    """Original approved manifest identity; no dependency URLs or raw content."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def recognized(self) -> Self:
        if self.path not in DEPENDENCY_PATHS:
            raise ValueError("DEPENDENCY_PATH_DENIED")
        return self


def dependency_digest(files: tuple[DependencyFile, ...]) -> str | None:
    """Version-one canonical path/hash fingerprint of the admitted subset."""
    if not files:
        return None
    payload = json.dumps(
        {"schema_version": "1.0", "files": [f.model_dump() for f in files]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class ContextFile(BaseModel):
    """Keep original and sanitized identities distinct."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    path: str = Field(max_length=240)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sanitized_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(max_length=4096, repr=False)
    reason: Literal["REQUESTED_PATH"] = "REQUESTED_PATH"
    symbols: tuple[str, ...] = Field(default=(), max_length=32)
    parse_status: Literal["PARSED", "INVALID_SYNTAX", "NOT_PYTHON"]


class ContextBundle(BaseModel):
    """Only sanitized payload for a future broker; no raw reasoning or credentials."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    source_commit: CommitSHA
    commit_identity: Literal["GIT_OBJECT_HASH", "GITHUB_HTTPS_METADATA"] = (
        "GIT_OBJECT_HASH"
    )
    evidence_origin: Literal["NOT_RECORDED", "TEST_TRANSPORT", "REMOTE_SERVICE"] = (
        "NOT_RECORDED"
    )
    files: tuple[ContextFile, ...] = Field(default=(), max_length=16)
    dependency_files: tuple[DependencyFile, ...] = Field(default=(), max_length=6)
    deps_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    sanitized_errors: str = Field(max_length=1024, repr=False)
    omissions: tuple[str, ...] = Field(default=(), max_length=18)
    trust: Literal["UNTRUSTED_REPOSITORY_DATA"] = "UNTRUSTED_REPOSITORY_DATA"
    can_authorize_execution: Literal[False] = False

    @model_validator(mode="after")
    def dependency_identity(self) -> Self:
        paths = tuple(f.path for f in self.dependency_files)
        if paths != tuple(sorted(set(paths))) or self.deps_hash != dependency_digest(
            self.dependency_files
        ):
            raise ValueError("DEPENDENCY_IDENTITY")
        return self
