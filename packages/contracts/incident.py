"""Sanitized incident artifact; never an instruction or execution grant."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IncidentReport(BaseModel):
    """Only redacted text may cross the durable incident boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    text: str = Field(max_length=1024, repr=False)
    truncated: bool = Field(strict=True)
    trust: Literal["UNTRUSTED_INCIDENT_DATA"] = "UNTRUSTED_INCIDENT_DATA"
    can_authorize_execution: Literal[False] = False
