"""Schema-validated model proposals; construction does not invoke a model."""

import ast
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.policies.repository import safe_tree_path


class RootCauseHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    claim: str = Field(min_length=1, max_length=1000)
    supporting_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    uncertainty: Literal["LOW", "MEDIUM", "HIGH"]


class ReproductionProposal(BaseModel):
    """An inert test artifact proposal, never local execution authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    target_path: str = Field(min_length=1, max_length=240)
    behavior: str = Field(min_length=1, max_length=1000)
    failure_signature: str = Field(min_length=1, max_length=256)
    test_source: str = Field(min_length=1, max_length=12000, repr=False)

    @field_validator("target_path")
    @classmethod
    def test_path_only(cls, value: str) -> str:
        if (
            not safe_tree_path(value)
            or not value.startswith("tests/")
            or not value.endswith(".py")
        ):
            raise ValueError("REPRODUCTION_PATH_DENIED")
        return value

    @field_validator("test_source")
    @classmethod
    def syntactically_valid_python(cls, value: str) -> str:
        """Reject malformed or ambiguous source before retaining the artifact."""
        if "\x00" in value:
            raise ValueError("REPRODUCTION_SOURCE_DENIED")
        try:
            ast.parse(value, mode="exec")
        except (SyntaxError, ValueError):
            raise ValueError("REPRODUCTION_SOURCE_DENIED") from None
        return value

    def test_source_sha256(self) -> str:
        """Return the immutable content identity used by later runner admission."""
        return hashlib.sha256(self.test_source.encode("utf-8")).hexdigest()


class TriageProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    hypotheses: tuple[RootCauseHypothesis, ...] = Field(min_length=1, max_length=3)
    reproduction: ReproductionProposal


class PatchProposal(BaseModel):
    """Untrusted suggested diff; never evidence of application or correctness."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    unified_diff: str = Field(min_length=1, max_length=500000, repr=False)
    files_changed: tuple[str, ...] = Field(min_length=1, max_length=25)
    justification: str = Field(min_length=1, max_length=1000)
    expected_test_delta: str = Field(min_length=1, max_length=1000)
    risk_notes: str = Field(min_length=1, max_length=1000)
