"""Bounded sanitized context; construction does not authenticate source provenance."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.run import CommitSHA


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
    files: tuple[ContextFile, ...] = Field(default=(), max_length=16)
    sanitized_errors: str = Field(max_length=1024, repr=False)
    omissions: tuple[str, ...] = Field(default=(), max_length=18)
    trust: Literal["UNTRUSTED_REPOSITORY_DATA"] = "UNTRUSTED_REPOSITORY_DATA"
    can_authorize_execution: Literal[False] = False
