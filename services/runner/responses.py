"""Validated subset of Nebius REST responses; raw output stays private."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProviderModel(BaseModel):
    """Ignore irrelevant provider fields but require fields used for evidence."""

    model_config = ConfigDict(hide_input_in_errors=True)


class Created(ProviderModel):
    """Known operation identity is required for cancellation and reconciliation."""

    uuid: UUID


class Stream(ProviderModel):
    """Encoded output; truncation status must be explicitly observed."""

    value: str = Field(max_length=8192, repr=False)
    encoding: Literal["base64", "ascii", "utf-8", "utf8"]
    truncated: bool = Field(strict=True)


class ProcessState(ProviderModel):
    """Service SUCCESS does not imply a successful guest process."""

    exit_code: int = Field(strict=True)
    signal: int | None = Field(strict=True)
    timed_out: bool = Field(strict=True)


class ProcessResult(ProviderModel):
    """Private bounded streams and process status."""

    state: ProcessState
    stdout: Stream = Field(repr=False)
    stderr: Stream = Field(repr=False)


class Operation(ProviderModel):
    """Instance status with source identity and private request/result metadata."""

    uuid: UUID
    kind: Literal["instance"]
    status: Literal[
        "PENDING", "ASSIGNED", "EXECUTING", "SUCCESS", "FAILED", "CANCELLED"
    ]
    image_uuid: UUID
    result_image_uuid: UUID | None
    metadata: dict[str, object] = Field(default_factory=dict, repr=False)
