"""Bounded source provenance without raw repository content or credentials."""

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from packages.contracts.run import CommitSHA
from packages.policies.repository import safe_tree_path


class SourceFile(BaseModel):
    """Original Git blob and SHA256 identities, never sanitized content identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    path: str = Field(min_length=1, max_length=240)
    blob_id: CommitSHA
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(strict=True, ge=0, le=65536)

    @field_validator("path")
    @classmethod
    def admitted_path_syntax(cls, value: str) -> str:
        if not safe_tree_path(value):
            raise ValueError("SOURCE_PATH_DENIED")
        return value


class SourceManifest(BaseModel):
    """Provider root binding and observed file hashes, not signed repair proof."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    repository_id: UUID
    source_commit: CommitSHA
    root_tree: CommitSHA
    evidence_origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    commit_identity: Literal["GITHUB_HTTPS_METADATA"] = "GITHUB_HTTPS_METADATA"
    raw_commit_verified: Literal[False] = False
    observed_at: AwareDatetime
    trace_id: UUID
    policy_version: Literal["repository-v1"] = "repository-v1"
    files: tuple[SourceFile, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def distinct_sorted_files(self) -> Self:
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(paths)) or len({p.lower() for p in paths}) != len(
            paths
        ):
            raise ValueError("SOURCE_PATH_COLLISION_OR_ORDER")
        return self


class SealedSnapshot(BaseModel):
    """Authenticated ciphertext envelope; no key or raw source fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    run_id: UUID
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plaintext_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    nonce: str = Field(pattern=r"^[a-f0-9]{24}$")
    ciphertext_b64: str = Field(min_length=24, max_length=4000032, repr=False)


class MaterializationSourceFile(BaseModel):
    """Base64 transfer for one raw, manifest-pinned source file."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    path: str = Field(min_length=1, max_length=240)
    content_b64: str = Field(max_length=87384, repr=False)


class MaterializationSourceResult(BaseModel):
    """Private worker-only transfer; constructing it grants no execution rights."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    version: int = Field(strict=True, ge=0)
    source_commit: CommitSHA
    manifest_ref: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    files: tuple[MaterializationSourceFile, ...] = Field(min_length=1, max_length=16)
    status: Literal["ADMITTED"] = "ADMITTED"
