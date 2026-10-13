"""Trusted repository grants; these are not user-submitted permission requests."""

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.contracts.run import CommitSHA


class RepositoryGrant(BaseModel):
    """One operator-owned consent record bound to exact immutable commits."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    repository_id: UUID
    owner_id: UUID
    remote_url: str = Field(max_length=240)
    commits: tuple[CommitSHA, ...] = Field(min_length=1, max_length=32)
    approved_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    consent: bool = Field(strict=True)
    private: bool = Field(default=False, strict=True)
    private_access_approved: bool = Field(default=False, strict=True)
    policy_version: Literal["repository-v1"] = "repository-v1"

    @field_validator("remote_url")
    @classmethod
    def canonical_remote(cls, value: str) -> str:
        """A canonical GitHub HTTPS origin only; no transport or URL guessing."""
        if not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/"
            r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}",
            value,
        ) or value.endswith((".git", ".")):
            raise ValueError("REPOSITORY_ORIGIN_DENIED")
        return value

    @field_validator("approved_paths")
    @classmethod
    def registered_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Initial fixed vocabulary; no model-authored glob expressions."""
        registered = {
            "src/",
            "app/",
            "tests/",
            "pyproject.toml",
            "requirements.txt",
            "README.md",
        }
        if len(set(values)) != len(values) or not set(values) <= registered:
            raise ValueError("REPOSITORY_PATH_POLICY_DENIED")
        return values


class ScopeDecision(BaseModel):
    """Safe metadata decision, distinct from authenticated provenance proof."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    allowed: bool
    policy_version: Literal["repository-v1"] = "repository-v1"
    reason: Literal["ALLOW", "SCOPE_DENIED"]
    approved_paths: tuple[str, ...] = ()
    tool_policy: Literal["NONE", "READ_ONLY_PINNED"] = "NONE"
